import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import require_permission
from app.models import Item, StockBalance, StockMovement, StockTransaction, User


router = APIRouter(prefix="/api")


class MonthlyLedgerReport(TypedDict):
    year: int
    month: int
    location_id: str | None
    rows: list[dict[str, object]]
    summary: dict[str, Decimal]


@router.get("/reports/low-stock")
def low_stock_report(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("reports:read")),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    return _low_stock_rows(session, limit=limit, offset=offset)


@router.get("/reports/valuation")
def valuation_report(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("reports:read")),
) -> dict[str, object]:
    return {
        "total_value": _total_valuation(session),
        "valuation_method": "standard_cost",
        "items": _valuation_rows(session),
    }


@router.get("/reports/consumption")
def consumption_report(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("reports:read")),
    days: int = Query(default=30, ge=1, le=3650),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    return _consumption_rows(session, days, limit=limit, offset=offset)


@router.get("/reports/monthly-ledger")
def monthly_ledger_report(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("reports:read")),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    location_id: str | None = None,
    category_id: str | None = None,
) -> MonthlyLedgerReport:
    return _monthly_ledger_rows(
        session,
        year=year,
        month=month,
        location_id=location_id,
        category_id=category_id,
    )


@router.get("/reports/{report_name}/export")
def export_report(
    report_name: str,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("reports:read")),
    format: str = Query(default="csv", pattern="^csv$"),
    days: int = Query(default=30, ge=1, le=3650),
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    location_id: str | None = None,
    category_id: str | None = None,
) -> StreamingResponse:
    if report_name == "low-stock":
        rows = _low_stock_rows(session)
    elif report_name == "consumption":
        rows = _consumption_rows(session, days)
    elif report_name == "valuation":
        rows = _valuation_rows(session) or [{"total_value": _total_valuation(session)}]
    elif report_name == "monthly-ledger":
        if year is None or month is None:
            raise HTTPException(
                status_code=422,
                detail="year and month are required for the monthly ledger",
            )
        report = _monthly_ledger_rows(
            session,
            year=year,
            month=month,
            location_id=location_id,
            category_id=category_id,
        )
        rows = _monthly_csv_rows(report)
    else:
        raise HTTPException(status_code=404, detail="Report not found")

    return _csv_response(report_name, rows)


@router.get("/reports/export")
def export_report_by_type(
    report_type: Literal[
        "low-stock", "valuation", "consumption", "monthly-ledger"
    ],
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("reports:read")),
    format: str = Query(default="csv", pattern="^csv$"),
    days: int = Query(default=30, ge=1, le=3650),
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    location_id: str | None = None,
    category_id: str | None = None,
) -> StreamingResponse:
    if report_type == "monthly-ledger":
        if year is None or month is None:
            raise HTTPException(
                status_code=422,
                detail="year and month are required for the monthly ledger",
            )
        report = _monthly_ledger_rows(
            session,
            year=year,
            month=month,
            location_id=location_id,
            category_id=category_id,
        )
        rows = _monthly_csv_rows(report)
    elif report_type == "low-stock":
        rows = _low_stock_rows(session)
    elif report_type == "consumption":
        rows = _consumption_rows(session, days)
    else:
        rows = _valuation_rows(session) or [{"total_value": _total_valuation(session)}]
    return _csv_response(report_type, rows)


def _csv_response(
    report_name: str, rows: list[dict[str, object]]
) -> StreamingResponse:
    output = io.StringIO(newline="")
    fields = {
        "low-stock": ["item_id", "sku", "name", "minimum_stock", "on_hand"],
        "consumption": [
            "item_id",
            "sku",
            "name",
            "quantity_consumed",
            "average_daily_consumed",
        ],
        "valuation": [
            "item_id",
            "sku",
            "name",
            "on_hand",
            "unit_cost",
            "total_value",
        ],
        "monthly-ledger": [
            "item_id",
            "sku",
            "name",
            "category_id",
            "opening_stock",
            "purchased_qty",
            "donated_qty",
            "other_receipt_qty",
            "new_stock",
            "total_stock",
            "requisition_issued_qty",
            "daily_dispensed_qty",
            "other_issued_qty",
            "stock_taken",
            "net_adjustments_and_transfers",
            "closing_stock",
        ],
    }[report_name]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_safe(value) for key, value in row.items()})
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="inventory-{report_name}.csv"'
        },
    )


def _monthly_ledger_rows(
    session: Session,
    *,
    year: int,
    month: int,
    location_id: str | None = None,
    category_id: str | None = None,
) -> MonthlyLedgerReport:
    period_start = datetime(year, month, 1, tzinfo=timezone.utc)
    period_end = (
        datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    )
    item_query = select(Item).where(Item.is_active.is_(True))
    if category_id is not None:
        item_query = item_query.where(Item.category_id == category_id)
    items = list(session.scalars(item_query.order_by(Item.name, Item.id)))
    item_ids = [item.id for item in items]
    if not item_ids:
        return {
            "year": year,
            "month": month,
            "location_id": location_id,
            "rows": [],
            "summary": _monthly_summary([]),
        }

    opening_query = (
        select(
            StockMovement.item_id,
            func.sum(StockMovement.quantity_delta).label("opening_stock"),
        )
        .join(StockTransaction, StockTransaction.id == StockMovement.transaction_id)
        .where(
            StockMovement.item_id.in_(item_ids),
            (StockTransaction.created_at < period_start)
            | (
                (StockTransaction.created_at >= period_start)
                & (StockTransaction.created_at < period_end)
                & (StockTransaction.transaction_subtype == "opening_balance")
            ),
        )
        .group_by(StockMovement.item_id)
    )
    if location_id is not None:
        opening_query = opening_query.where(
            StockMovement.location_id == location_id
        )
    opening = {
        row.item_id: Decimal(str(row.opening_stock or 0))
        for row in session.execute(opening_query)
    }

    period_query = (
        select(
            StockMovement.item_id,
            StockMovement.quantity_delta,
            StockTransaction.transaction_type,
            StockTransaction.purchase_order_id,
            StockTransaction.requisition_id,
            StockTransaction.transaction_subtype,
            StockTransaction.donation_batch_id,
            StockTransaction.daily_care_log_id,
        )
        .join(StockTransaction, StockTransaction.id == StockMovement.transaction_id)
        .where(
            StockMovement.item_id.in_(item_ids),
            StockTransaction.created_at >= period_start,
            StockTransaction.created_at < period_end,
        )
    )
    if location_id is not None:
        period_query = period_query.where(StockMovement.location_id == location_id)

    metric_names = (
        "purchased_qty",
        "donated_qty",
        "other_receipt_qty",
        "requisition_issued_qty",
        "daily_dispensed_qty",
        "other_issued_qty",
        "net_adjustments_and_transfers",
    )
    totals: dict[str, dict[str, Decimal]] = {
        item_id: {field: Decimal("0") for field in metric_names}
        for item_id in item_ids
    }
    for movement in session.execute(period_query):
        delta = Decimal(str(movement.quantity_delta))
        values = totals[movement.item_id]
        subtype = movement.transaction_subtype
        if delta > 0 and movement.transaction_type == "receipt":
            if movement.purchase_order_id is not None or subtype == "purchase_receipt":
                values["purchased_qty"] += delta
            elif movement.donation_batch_id is not None or subtype == "donation_receipt":
                values["donated_qty"] += delta
            elif subtype != "opening_balance":
                values["other_receipt_qty"] += delta
        elif delta < 0 and movement.transaction_type == "issue":
            if movement.requisition_id is not None or subtype == "requisition_issue":
                values["requisition_issued_qty"] += -delta
            elif movement.daily_care_log_id is not None or subtype == "daily_dispense":
                values["daily_dispensed_qty"] += -delta
            else:
                values["other_issued_qty"] += -delta
        elif movement.transaction_type in {"adjustment", "transfer"}:
            values["net_adjustments_and_transfers"] += delta

    rows: list[dict[str, object]] = []
    for item in items:
        quantities = totals[item.id]
        opening_stock = opening.get(item.id, Decimal("0"))
        purchased_qty = quantities["purchased_qty"]
        donated_qty = quantities["donated_qty"]
        other_receipt_qty = quantities["other_receipt_qty"]
        new_stock = purchased_qty + donated_qty + other_receipt_qty
        total_stock = opening_stock + new_stock
        requisition_issued_qty = quantities["requisition_issued_qty"]
        daily_dispensed_qty = quantities["daily_dispensed_qty"]
        other_issued_qty = quantities["other_issued_qty"]
        stock_taken = (
            requisition_issued_qty + daily_dispensed_qty + other_issued_qty
        )
        adjustments = quantities["net_adjustments_and_transfers"]
        rows.append(
            {
                "item_id": item.id,
                "sku": item.sku,
                "name": item.name,
                "category_id": item.category_id,
                "opening_stock": opening_stock,
                "purchased_qty": purchased_qty,
                "donated_qty": donated_qty,
                "other_receipt_qty": other_receipt_qty,
                "new_stock": new_stock,
                "total_stock": total_stock,
                "requisition_issued_qty": requisition_issued_qty,
                "daily_dispensed_qty": daily_dispensed_qty,
                "other_issued_qty": other_issued_qty,
                "stock_taken": stock_taken,
                "net_adjustments_and_transfers": adjustments,
                "closing_stock": total_stock - stock_taken + adjustments,
            }
        )
    return {
        "year": year,
        "month": month,
        "location_id": location_id,
        "rows": rows,
        "summary": _monthly_summary(rows),
    }


def _monthly_summary(rows: list[dict[str, object]]) -> dict[str, Decimal]:
    fields = (
        "opening_stock",
        "purchased_qty",
        "donated_qty",
        "other_receipt_qty",
        "new_stock",
        "total_stock",
        "requisition_issued_qty",
        "daily_dispensed_qty",
        "other_issued_qty",
        "stock_taken",
        "net_adjustments_and_transfers",
        "closing_stock",
    )
    return {
        field: sum(
            (Decimal(str(row[field])) for row in rows),
            start=Decimal("0"),
        )
        for field in fields
    }


def _monthly_csv_rows(report: MonthlyLedgerReport) -> list[dict[str, object]]:
    rows = list(report["rows"])
    rows.append(
        {
            "sku": "",
            "name": "TOTALS",
            "category_id": "",
            **report["summary"],
        }
    )
    return rows


def _low_stock_rows(
    session: Session, *, limit: int | None = None, offset: int = 0
) -> list[dict[str, object]]:
    query = (
        select(
            Item.id.label("item_id"),
            Item.sku,
            Item.name,
            Item.minimum_stock,
            func.coalesce(func.sum(StockBalance.quantity), 0).label("on_hand"),
        )
        .outerjoin(StockBalance, StockBalance.item_id == Item.id)
        .where(Item.is_active.is_(True))
        .group_by(Item.id)
        .having(func.coalesce(func.sum(StockBalance.quantity), 0) < Item.minimum_stock)
        .order_by(Item.name, Item.id)
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return [dict(row._mapping) for row in session.execute(query)]


def _consumption_rows(
    session: Session,
    days: int,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, object]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    consumed = (
        select(
            StockMovement.item_id,
            func.sum(-StockMovement.quantity_delta).label("quantity_consumed"),
        )
        .join(StockTransaction, StockTransaction.id == StockMovement.transaction_id)
        .where(
            StockTransaction.transaction_type == "issue",
            StockTransaction.created_at >= cutoff,
        )
        .group_by(StockMovement.item_id)
        .subquery()
    )
    query = (
        select(
            Item.id.label("item_id"),
            Item.sku,
            Item.name,
            func.coalesce(consumed.c.quantity_consumed, 0).label("quantity_consumed"),
        )
        .outerjoin(consumed, consumed.c.item_id == Item.id)
        .where(Item.is_active.is_(True))
        .order_by(Item.name, Item.id)
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    rows = [dict(row._mapping) for row in session.execute(query)]
    for row in rows:
        consumed_qty = Decimal(str(row["quantity_consumed"]))
        row["average_daily_consumed"] = consumed_qty / Decimal(days)
    return rows


def _valuation_rows(session: Session) -> list[dict[str, object]]:
    query = (
        select(
            Item.id.label("item_id"),
            Item.sku,
            Item.name,
            Item.unit_cost,
            func.coalesce(func.sum(StockBalance.quantity), 0).label("on_hand"),
            func.coalesce(func.sum(StockBalance.quantity * Item.unit_cost), 0).label(
                "total_value"
            ),
        )
        .outerjoin(StockBalance, StockBalance.item_id == Item.id)
        .where(Item.is_active.is_(True))
        .group_by(Item.id)
        .order_by(Item.name, Item.id)
    )
    return [dict(row._mapping) for row in session.execute(query)]


def _total_valuation(session: Session) -> Decimal:
    value = session.scalar(
        select(func.coalesce(func.sum(StockBalance.quantity * Item.unit_cost), 0))
        .join(Item, Item.id == StockBalance.item_id)
        .where(Item.is_active.is_(True))
    )
    return value or Decimal("0")


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value
