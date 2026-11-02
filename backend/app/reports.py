import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dependencies import require_permission
from app.db import get_db
from app.models import Item, StockBalance, StockMovement, StockTransaction, User


router = APIRouter(prefix="/api")


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
) -> dict[str, Decimal | str]:
    return {
        "total_value": _total_valuation(session),
        "valuation_method": "standard_cost",
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


@router.get("/reports/{report_name}/export")
def export_report(
    report_name: str,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("reports:read")),
    format: str = Query(default="csv", pattern="^csv$"),
    days: int = Query(default=30, ge=1, le=3650),
) -> StreamingResponse:
    if report_name == "low-stock":
        rows = _low_stock_rows(session)
    elif report_name == "consumption":
        rows = _consumption_rows(session, days)
    elif report_name == "valuation":
        rows = [{"total_value": _total_valuation(session)}]
    else:
        raise HTTPException(status_code=404, detail="Report not found")

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
        "valuation": ["total_value"],
    }[report_name]
    writer = csv.DictWriter(output, fieldnames=fields)
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


def _total_valuation(session: Session) -> Decimal:
    value = session.scalar(
        select(func.coalesce(func.sum(StockBalance.quantity * Item.unit_cost), 0))
        .join(Item, Item.id == StockBalance.item_id)
    )
    return value or Decimal("0")


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value