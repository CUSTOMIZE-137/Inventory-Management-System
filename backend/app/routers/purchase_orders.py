from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import require_permission
from app.models import (
    Item,
    PurchaseOrder,
    PurchaseOrderLine,
    StockTransaction,
    User,
    Vendor,
)
from app.routers.inventory import load_transaction
from app.schemas import (
    MovementInput,
    PurchaseOrderCreate,
    PurchaseOrderLineRead,
    PurchaseOrderRead,
    PurchaseOrderReceive,
    TransactionRead,
)
from app.services.inventory import post_stock_transaction

router = APIRouter(prefix="/api")


def _po_response(session: Session, order: PurchaseOrder) -> PurchaseOrderRead:
    lines = list(
        session.scalars(
            select(PurchaseOrderLine)
            .where(PurchaseOrderLine.purchase_order_id == order.id)
            .order_by(PurchaseOrderLine.id)
        )
    )
    return PurchaseOrderRead(
        id=order.id,
        vendor_id=order.vendor_id,
        created_by=order.created_by,
        reference=order.reference,
        status=order.status,
        created_at=order.created_at,
        lines=[PurchaseOrderLineRead.model_validate(line) for line in lines],
    )


@router.get("/purchase-orders", response_model=list[PurchaseOrderRead])
def list_purchase_orders(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
    status: str | None = None,
    vendor_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[PurchaseOrderRead]:
    query = select(PurchaseOrder)
    if status:
        query = query.where(PurchaseOrder.status == status)
    if vendor_id:
        query = query.where(PurchaseOrder.vendor_id == vendor_id)
    orders = list(
        session.scalars(
            query.order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return [_po_response(session, order) for order in orders]


@router.get("/purchase-orders/{order_id}", response_model=PurchaseOrderRead)
def get_purchase_order(
    order_id: str,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
) -> PurchaseOrderRead:
    order = session.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return _po_response(session, order)


@router.post("/purchase-orders", response_model=PurchaseOrderRead, status_code=201)
def create_purchase_order(
    payload: PurchaseOrderCreate,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("catalog:write")),
) -> PurchaseOrderRead:
    vendor = session.get(Vendor, payload.vendor_id)
    if vendor is None or not vendor.is_active:
        raise HTTPException(status_code=404, detail="Active vendor not found")
    if (
        session.scalar(
            select(PurchaseOrder.id).where(
                PurchaseOrder.reference == payload.reference
            )
        )
        is not None
    ):
        raise HTTPException(
            status_code=409, detail="Purchase order reference already exists"
        )
    requested_item_ids = {line.item_id for line in payload.lines}
    active_item_ids = set(
        session.scalars(
            select(Item.id).where(
                Item.id.in_(requested_item_ids), Item.is_active.is_(True)
            )
        )
    )
    if active_item_ids != requested_item_ids:
        raise HTTPException(status_code=404, detail="Active item not found")

    order = PurchaseOrder(
        vendor_id=payload.vendor_id,
        created_by=user.id,
        reference=payload.reference,
    )
    session.add(order)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Purchase order creation conflict"
        ) from error
    for line in payload.lines:
        session.add(
            PurchaseOrderLine(
                purchase_order_id=order.id,
                item_id=line.item_id,
                ordered_qty=line.ordered_qty,
                unit_cost=line.unit_cost,
            )
        )
    session.flush()
    return _po_response(session, order)


@router.post(
    "/purchase-orders/{order_id}/receive", response_model=TransactionRead
)
def receive_purchase_order(
    order_id: str,
    payload: PurchaseOrderReceive,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("inventory:transact")),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StockTransaction:
    order = session.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == order_id)
        .with_for_update()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    prior_transaction = (
        session.scalar(
            select(StockTransaction).where(
                StockTransaction.idempotency_key == idempotency_key
            )
        )
        if idempotency_key
        else None
    )
    is_replay = prior_transaction is not None
    if order.status not in {"open", "partially_received"} and not is_replay:
        raise HTTPException(
            status_code=404, detail="Open purchase order not found"
        )

    line_ids = sorted({r.purchase_order_line_id for r in payload.lines})
    locked_lines: dict[str, PurchaseOrderLine] = {}
    for line_id in line_ids:
        line = session.scalar(
            select(PurchaseOrderLine)
            .where(
                PurchaseOrderLine.id == line_id,
                PurchaseOrderLine.purchase_order_id == order.id,
            )
            .with_for_update()
        )
        if line is None:
            raise HTTPException(
                status_code=404, detail="Purchase order line not found"
            )
        locked_lines[line_id] = line

    consolidated_by_line: dict[str, Decimal] = {}
    receipt_movements: list[MovementInput] = []
    for received in payload.lines:
        line = locked_lines[received.purchase_order_line_id]
        consolidated_by_line[line.id] = (
            consolidated_by_line.get(line.id, Decimal("0")) + received.quantity
        )
        receipt_movements.append(
            MovementInput(
                item_id=line.item_id,
                location_id=received.location_id,
                quantity_delta=received.quantity,
            )
        )

    if not is_replay:
        for line_id, total_receiving in consolidated_by_line.items():
            line = locked_lines[line_id]
            if line.received_qty + total_receiving > line.ordered_qty:
                raise HTTPException(
                    status_code=409, detail="Receipt exceeds ordered quantity"
                )

    transaction = post_stock_transaction(
        session,
        transaction_type="receipt",
        actor_id=user.id,
        movements=receipt_movements,
        reason=payload.reason,
        reference=order.reference,
        idempotency_key=idempotency_key,
        purchase_order_id=order.id,
    )
    if is_replay:
        return load_transaction(session, transaction.id)

    for line_id, total_receiving in consolidated_by_line.items():
        locked_lines[line_id].received_qty += total_receiving

    all_lines = list(
        session.scalars(
            select(PurchaseOrderLine).where(
                PurchaseOrderLine.purchase_order_id == order.id
            )
        )
    )
    order.status = (
        "received"
        if all(line.received_qty == line.ordered_qty for line in all_lines)
        else "partially_received"
    )
    session.flush()
    return load_transaction(session, transaction.id)
