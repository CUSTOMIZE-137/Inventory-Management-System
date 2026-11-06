import hashlib
import json
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import (
    Item,
    Location,
    Requisition,
    RequisitionLine,
    StockBalance,
    StockMovement,
    StockTransaction,
    UnitConversion,
)
from app.schemas import MovementInput


def _insert_missing_balance(session: Session, item_id: str, location_id: str) -> None:
    # A zero row lets the later lock-and-update path use the same logic for
    # first-time stock and existing balances.
    values = {"item_id": item_id, "location_id": location_id, "quantity": Decimal("0")}
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = pg_insert(StockBalance).values(**values).on_conflict_do_nothing()
    elif dialect == "sqlite":
        statement = sqlite_insert(StockBalance).values(**values).on_conflict_do_nothing()
    else:
        raise RuntimeError(f"Unsupported database dialect for stock writes: {dialect}")
    session.execute(statement)


def post_stock_transaction(
    session: Session,
    *,
    transaction_type: str,
    actor_id: str,
    movements: list[MovementInput],
    reason: str,
    reference: str | None = None,
    idempotency_key: str | None = None,
    recipient: str | None = None,
    purchase_order_id: str | None = None,
    requisition_id: str | None = None,
    transaction_subtype: str | None = None,
    donation_batch_id: str | None = None,
    daily_care_log_id: str | None = None,
    stock_take_session_id: str | None = None,
) -> StockTransaction:
    """Validate, lock, and atomically post stock movements and their audit rows."""
    if not reason.strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
        raise HTTPException(
            status_code=422, detail="Idempotency-Key must be between 1 and 128 characters"
        )
    if transaction_type not in {"receipt", "issue", "adjustment", "transfer"}:
        raise HTTPException(status_code=422, detail="Unsupported stock transaction type")

    consolidated: dict[tuple[str, str], Decimal] = {}
    for movement in movements:
        quantity = movement.quantity_delta
        if not quantity.is_finite() or quantity == 0:
            raise HTTPException(
                status_code=422, detail="Movement quantities must be finite and non-zero"
            )
        if quantity.as_tuple().exponent < -3:
            raise HTTPException(
                status_code=422, detail="Movement quantities support at most 3 decimals"
            )
        key = (movement.item_id, movement.location_id)
        consolidated[key] = consolidated.get(key, Decimal("0")) + quantity
    consolidated = {key: qty for key, qty in consolidated.items() if qty != 0}
    if not consolidated:
        raise HTTPException(status_code=422, detail="Transaction has no stock changes")
    if transaction_type == "receipt" and any(qty <= 0 for qty in consolidated.values()):
        raise HTTPException(status_code=422, detail="Receipt quantities must be positive")
    if transaction_type == "issue" and any(qty >= 0 for qty in consolidated.values()):
        raise HTTPException(status_code=422, detail="Issue quantities must be negative")
    if transaction_type == "transfer":
        item_totals: dict[str, Decimal] = {}
        for (item_id, _location_id), qty in consolidated.items():
            item_totals[item_id] = item_totals.get(item_id, Decimal("0")) + qty
        if any(total != 0 for total in item_totals.values()):
            raise HTTPException(
                status_code=422, detail="Transfer movements must balance per item"
            )

    inferred_subtype = (
        "purchase_receipt"
        if purchase_order_id is not None and transaction_type == "receipt"
        else "requisition_issue"
        if requisition_id is not None and transaction_type == "issue"
        else "donation_receipt"
        if donation_batch_id is not None
        else "daily_dispense"
        if daily_care_log_id is not None
        else "stock_take_adjustment"
        if stock_take_session_id is not None
        else None
    )
    effective_subtype = transaction_subtype or inferred_subtype
    canonical = {
        "type": transaction_type,
        "movements": [
            [item_id, location_id, str(quantity)]
            for (item_id, location_id), quantity in sorted(consolidated.items())
        ],
        "reason": reason.strip(),
        "reference": reference,
        "recipient": recipient,
        "purchase_order_id": purchase_order_id,
        "requisition_id": requisition_id,
    }
    if transaction_subtype is not None:
        canonical["transaction_subtype"] = transaction_subtype
    if donation_batch_id is not None:
        canonical["donation_batch_id"] = donation_batch_id
    if daily_care_log_id is not None:
        canonical["daily_care_log_id"] = daily_care_log_id
    if stock_take_session_id is not None:
        canonical["stock_take_session_id"] = stock_take_session_id
    request_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    if idempotency_key:
        existing = session.scalar(
            select(StockTransaction).where(
                StockTransaction.idempotency_key == idempotency_key
            )
        )
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different request",
                )
            return existing

    # Create and lock rows in a deterministic order to avoid transfer deadlocks.
    # The active-record checks happen before the balance lock so invalid
    # references cannot create partial inventory changes.
    ordered_keys = sorted(consolidated)
    active_items = {
        item.id: item
        for item in session.scalars(
            select(Item).where(
                Item.id.in_({key[0] for key in ordered_keys}),
                Item.is_active.is_(True),
            )
        )
    }
    if set(active_items) != {key[0] for key in ordered_keys}:
        raise HTTPException(status_code=404, detail="Active item not found")
    locations = {
        location.id: location
        for location in session.scalars(
            select(Location).where(Location.id.in_({key[1] for key in ordered_keys}))
        )
    }
    if any(
        location_id not in locations or not locations[location_id].is_active
        for _item_id, location_id in ordered_keys
    ):
        raise HTTPException(status_code=404, detail="Active location not found")
    if transaction_type == "issue":
        restricted_item_ids = {
            item.id
            for item in active_items.values()
            if item.is_restricted or item.requires_approval
        }
        if restricted_item_ids and requisition_id is None:
            raise HTTPException(
                status_code=403,
                detail="Restricted items must be issued through an approved requisition",
            )
        if requisition_id is not None:
            requisition = session.scalar(
                select(Requisition)
                .where(Requisition.id == requisition_id)
                .with_for_update()
            )
            if requisition is None or requisition.status != "approved_for_dispatch":
                raise HTTPException(
                    status_code=409, detail="Requisition is not approved for dispatch"
                )
            approved: dict[str, Decimal] = {}
            for line in session.scalars(
                select(RequisitionLine).where(
                    RequisitionLine.requisition_id == requisition_id
                )
            ):
                approved[line.item_id] = (
                    approved.get(line.item_id, Decimal("0"))
                    + line.quantity_approved
                )
            issued: dict[str, Decimal] = {}
            for (item_id, _location_id), quantity in consolidated.items():
                issued[item_id] = issued.get(item_id, Decimal("0")) - quantity
            if issued != {item_id: qty for item_id, qty in approved.items() if qty > 0}:
                raise HTTPException(
                    status_code=403,
                    detail="Dispatch quantities must match the approved requisition",
                )
    for item_id, location_id in ordered_keys:
        _insert_missing_balance(session, item_id, location_id)

    balances: dict[tuple[str, str], StockBalance] = {}
    for item_id, location_id in ordered_keys:
        balance = session.scalar(
            select(StockBalance)
            .where(
                StockBalance.item_id == item_id,
                StockBalance.location_id == location_id,
            )
            .with_for_update()
        )
        if balance is None:
            raise RuntimeError("Stock balance row was not created or found")
        balances[(item_id, location_id)] = balance

    for item_id, location_id in ordered_keys:
        balance = balances[(item_id, location_id)]
        after = balance.quantity + consolidated[(item_id, location_id)]
        if after < 0:
            raise HTTPException(status_code=409, detail="Insufficient stock")

    transaction_values = dict(
        transaction_type=transaction_type,
        actor_id=actor_id,
        reference=reference,
        reason=reason.strip(),
        recipient=recipient,
        purchase_order_id=purchase_order_id,
        requisition_id=requisition_id,
        transaction_subtype=effective_subtype,
        donation_batch_id=donation_batch_id,
        daily_care_log_id=daily_care_log_id,
        stock_take_session_id=stock_take_session_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash if idempotency_key else None,
    )
    if idempotency_key:
        # The unique key protects concurrent retries; the request hash prevents
        # a reused key from silently changing the original operation.
        dialect = session.get_bind().dialect.name
        insert_statement = (
            pg_insert(StockTransaction)
            if dialect == "postgresql"
            else sqlite_insert(StockTransaction)
            if dialect == "sqlite"
            else None
        )
        if insert_statement is None:
            raise RuntimeError(f"Unsupported database dialect for stock writes: {dialect}")
        transaction_id = session.scalar(
            insert_statement.values(**transaction_values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(StockTransaction.id)
        )
        if transaction_id is None:
            existing = session.scalar(
                select(StockTransaction).where(
                    StockTransaction.idempotency_key == idempotency_key
                )
            )
            if existing is None:
                raise RuntimeError("Idempotent transaction conflict could not be resolved")
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different request",
                )
            return existing
        transaction = session.get(StockTransaction, transaction_id)
        if transaction is None:
            raise RuntimeError("Stock transaction row was not created or found")
    else:
        transaction = StockTransaction(**transaction_values)
        session.add(transaction)
        session.flush()

    for key in ordered_keys:
        balance = balances[key]
        before = balance.quantity
        delta = consolidated[key]
        after = before + delta
        balance.quantity = after
        # Movements capture both sides of the balance change for audit and
        # reporting; the balance is the current-state projection.
        session.add(
            StockMovement(
                transaction_id=transaction.id,
                item_id=key[0],
                location_id=key[1],
                quantity_delta=delta,
                quantity_before=before,
                quantity_after=after,
            )
        )
    session.flush()
    return transaction


def normalize_item_quantity(
    session: Session,
    item: Item,
    quantity: Decimal,
    uom_code: str | None,
) -> Decimal:
    # All stock tables use the item's base unit, while callers may enter a
    # configured issue or receiving unit.
    if uom_code is None or uom_code == item.uom_code:
        normalized = quantity.quantize(Decimal("0.001"))
    else:
        conversion = session.scalar(
            select(UnitConversion).where(
                UnitConversion.item_id == item.id,
                UnitConversion.from_uom == uom_code,
                UnitConversion.to_uom == item.uom_code,
            )
        )
        if conversion is not None:
            normalized = (quantity * conversion.multiplier).quantize(
                Decimal("0.001")
            )
        else:
            inverse = session.scalar(
                select(UnitConversion).where(
                    UnitConversion.item_id == item.id,
                    UnitConversion.from_uom == item.uom_code,
                    UnitConversion.to_uom == uom_code,
                )
            )
            if inverse is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"No unit conversion defined for item {item.sku} "
                        f"from {uom_code} to {item.uom_code}"
                    ),
                )
            normalized = (quantity / inverse.multiplier).quantize(
                Decimal("0.001")
            )
    if normalized <= 0:
        raise HTTPException(
            status_code=422,
            detail="Quantity is too small to represent in the item's base unit",
        )
    return normalized


def post_transfer(
    session: Session,
    *,
    actor_id: str,
    item_id: str,
    source_location_id: str,
    destination_location_id: str,
    quantity: Decimal,
    reason: str,
    reference: str | None = None,
    idempotency_key: str | None = None,
) -> StockTransaction:
    if not quantity.is_finite() or quantity <= 0:
        raise HTTPException(status_code=422, detail="Transfer quantity must be positive")
    if source_location_id == destination_location_id:
        raise HTTPException(status_code=422, detail="Source and destination must differ")
    return post_stock_transaction(
        session,
        transaction_type="transfer",
        actor_id=actor_id,
        movements=[
            MovementInput(
                item_id=item_id,
                location_id=source_location_id,
                quantity_delta=-quantity,
            ),
            MovementInput(
                item_id=item_id,
                location_id=destination_location_id,
                quantity_delta=quantity,
            ),
        ],
        reason=reason,
        reference=reference,
        idempotency_key=idempotency_key,
    )
