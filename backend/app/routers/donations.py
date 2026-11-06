from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.dependencies import current_user, require_permission
from app.models import (
    DonationBatch,
    DonationBatchLine,
    Donor,
    Item,
    StockTransaction,
    User,
)
from app.schemas import (
    DonationBatchCreate,
    DonationBatchRead,
    DonorCreate,
    DonorRead,
)
from app.services.inventory import normalize_item_quantity, post_stock_transaction
from app.schemas import MovementInput

router = APIRouter(prefix="/api")


def _load_batch(session: Session, batch_id: str) -> dict[str, object]:
    batch = session.scalar(
        select(DonationBatch)
        .options(selectinload(DonationBatch.lines))
        .where(DonationBatch.id == batch_id)
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="Donation batch not found")
    transaction_id = session.scalar(
        select(StockTransaction.id).where(
            StockTransaction.donation_batch_id == batch.id
        )
    )
    return {
        "id": batch.id,
        "donor_id": batch.donor_id,
        "received_by": batch.received_by,
        "reference": batch.reference,
        "received_date": batch.received_date,
        "notes": batch.notes,
        "created_at": batch.created_at,
        "transaction_id": transaction_id,
        "lines": batch.lines,
    }


def _line_signature(
    item_id: str,
    location_id: str,
    quantity: Decimal,
    uom_code: str | None,
    estimated_unit_cost: Decimal,
) -> tuple[str, str, str, str | None, str]:
    return (
        item_id,
        location_id,
        str(quantity.quantize(Decimal("0.001"))),
        uom_code,
        str(estimated_unit_cost.quantize(Decimal("0.0001"))),
    )


def _matching_existing_batch(
    session: Session,
    payload: DonationBatchCreate,
    actor_id: str,
    idempotency_key: str,
) -> DonationBatch | None:
    # A retry is safe only when its actor, metadata, and complete line set
    # match the original request associated with the idempotency key.
    transaction = session.scalar(
        select(StockTransaction).where(
            StockTransaction.idempotency_key == idempotency_key
        )
    )
    if transaction is None:
        return None
    if transaction.donation_batch_id is None:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different request",
        )
    batch = session.scalar(
        select(DonationBatch)
        .options(selectinload(DonationBatch.lines))
        .where(DonationBatch.id == transaction.donation_batch_id)
    )
    if (
        batch is None
        or batch.received_by != actor_id
        or batch.donor_id != payload.donor_id
        or batch.reference != payload.reference
        or batch.notes != payload.notes
    ):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different request",
        )
    existing_lines = Counter(
        _line_signature(
            line.item_id,
            line.location_id,
            line.quantity,
            line.uom_code,
            line.estimated_unit_cost,
        )
        for line in batch.lines
    )
    requested_lines = Counter(
        _line_signature(
            line.item_id,
            line.location_id,
            line.quantity,
            line.uom_code,
            line.estimated_unit_cost,
        )
        for line in payload.lines
    )
    if existing_lines != requested_lines:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different request",
        )
    return batch


@router.post("/donors", response_model=DonorRead, status_code=status.HTTP_201_CREATED)
def create_donor(
    payload: DonorCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> Donor:
    donor = Donor(**payload.model_dump())
    session.add(donor)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Donor name already exists") from error
    return donor


@router.get("/donors", response_model=list[DonorRead])
def list_donors(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Donor]:
    query = select(Donor)
    if not include_inactive:
        query = query.where(Donor.is_active.is_(True))
    return list(
        session.scalars(query.order_by(Donor.name).limit(limit).offset(offset))
    )


@router.post(
    "/donations",
    response_model=DonationBatchRead,
    status_code=status.HTTP_201_CREATED,
)
def create_donation(
    payload: DonationBatchCreate,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    _permission: User = Depends(require_permission("inventory:transact")),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    if idempotency_key:
        existing = _matching_existing_batch(session, payload, user.id, idempotency_key)
        if existing is not None:
            return _load_batch(session, existing.id)
    if payload.donor_id is not None:
        donor = session.get(Donor, payload.donor_id)
        if donor is None or not donor.is_active:
            raise HTTPException(status_code=404, detail="Active donor not found")

    items: dict[str, Item] = {}
    normalized_lines = []
    for line in payload.lines:
        item = items.get(line.item_id)
        if item is None:
            item = session.get(Item, line.item_id)
            if item is None or not item.is_active:
                raise HTTPException(status_code=404, detail="Active item not found")
            items[item.id] = item
        normalized_lines.append(
            (
                line,
                normalize_item_quantity(
                    session, item, line.quantity, line.uom_code
                ),
            )
        )

    # Preserve the entered units on the donation document while posting the
    # normalized base-unit quantities to the canonical stock ledger.
    batch = DonationBatch(
        donor_id=payload.donor_id,
        received_by=user.id,
        reference=payload.reference,
        notes=payload.notes,
    )
    for line, _normalized in normalized_lines:
        batch.lines.append(DonationBatchLine(**line.model_dump()))
    session.add(batch)
    try:
        session.flush()
        transaction = post_stock_transaction(
            session,
            transaction_type="receipt",
            transaction_subtype="donation_receipt",
            donation_batch_id=batch.id,
            actor_id=user.id,
            movements=[
                MovementInput(
                    item_id=line.item_id,
                    location_id=line.location_id,
                    quantity_delta=normalized,
                )
                for line, normalized in normalized_lines
            ],
            reason=f"Donation received: {payload.reference}",
            reference=payload.reference,
            idempotency_key=idempotency_key,
        )
        if transaction.donation_batch_id != batch.id:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used for a different request",
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Donation batch conflicts with an existing record"
        ) from error
    return _load_batch(session, batch.id)


@router.get("/donations", response_model=list[DonationBatchRead])
def list_donations(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
    donor_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    query = select(DonationBatch.id)
    if donor_id is not None:
        query = query.where(DonationBatch.donor_id == donor_id)
    batch_ids = list(
        session.scalars(
            query.order_by(DonationBatch.received_date.desc(), DonationBatch.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return [_load_batch(session, batch_id) for batch_id in batch_ids]


@router.get("/donations/monthly-summary")
def donation_monthly_summary(
    year: int = Query(..., ge=2000, le=2100),
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
) -> dict[str, object]:
    period_start = datetime(year, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    items = list(session.scalars(select(Item).order_by(Item.name, Item.sku)))
    rows = {
        item.id: {
            "item_id": item.id,
            "sku": item.sku,
            "name": item.name,
            "category_id": item.category_id,
            "uom_code": item.uom_code,
            "monthly_quantities": [Decimal("0.000") for _ in range(12)],
            "total_quantity": Decimal("0.000"),
        }
        for item in items
    }

    receipt_lines = session.execute(
        select(
            DonationBatch.received_date,
            DonationBatchLine.item_id,
            DonationBatchLine.quantity,
            DonationBatchLine.uom_code,
        )
        .join(DonationBatchLine, DonationBatchLine.batch_id == DonationBatch.id)
        .where(
            DonationBatch.received_date >= period_start,
            DonationBatch.received_date < period_end,
        )
        .order_by(DonationBatch.received_date, DonationBatch.id)
    )
    items_by_id = {item.id: item for item in items}
    for received_date, item_id, quantity, uom_code in receipt_lines:
        item = items_by_id[item_id]
        normalized_quantity = normalize_item_quantity(
            session, item, quantity, uom_code
        )
        month = (
            received_date.replace(tzinfo=timezone.utc)
            if received_date.tzinfo is None
            else received_date.astimezone(timezone.utc)
        ).month - 1
        row = rows[item_id]
        row["monthly_quantities"][month] += normalized_quantity
        row["total_quantity"] += normalized_quantity

    return {"year": year, "rows": list(rows.values())}


@router.get("/donations/{batch_id}", response_model=DonationBatchRead)
def get_donation(
    batch_id: str,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
) -> dict[str, object]:
    return _load_batch(session, batch_id)
