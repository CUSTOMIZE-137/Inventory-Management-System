from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.dependencies import current_user, require_permission
from app.models import StockBalance, StockMovement, StockTransaction, User
from app.schemas import (
    StockLevelRead,
    StockMovementRead,
    StockPostRequest,
    TransactionRead,
    TransferRequest,
)
from app.services.inventory import post_stock_transaction, post_transfer

router = APIRouter(prefix="/api")


def load_transaction(session: Session, transaction_id: str) -> StockTransaction:
    transaction = session.scalar(
        select(StockTransaction)
        .options(selectinload(StockTransaction.movements))
        .where(StockTransaction.id == transaction_id)
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@router.get("/stock", response_model=list[StockLevelRead])
def get_stock(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
    item_id: str | None = None,
    location_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[StockBalance]:
    query = select(StockBalance)
    if item_id:
        query = query.where(StockBalance.item_id == item_id)
    if location_id:
        query = query.where(StockBalance.location_id == location_id)
    return list(
        session.scalars(
            query.order_by(StockBalance.item_id, StockBalance.location_id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/transactions", response_model=list[TransactionRead])
def list_transactions(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
    transaction_type: str | None = None,
    item_id: str | None = None,
    location_id: str | None = None,
    reference: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[StockTransaction]:
    query = select(StockTransaction).options(
        selectinload(StockTransaction.movements)
    )
    if transaction_type:
        query = query.where(
            StockTransaction.transaction_type == transaction_type
        )
    if reference:
        query = query.where(StockTransaction.reference == reference)
    if item_id or location_id:
        movement_filter = select(StockMovement.transaction_id)
        if item_id:
            movement_filter = movement_filter.where(
                StockMovement.item_id == item_id
            )
        if location_id:
            movement_filter = movement_filter.where(
                StockMovement.location_id == location_id
            )
        query = query.where(StockTransaction.id.in_(movement_filter))
    return list(
        session.scalars(
            query.order_by(StockTransaction.created_at.desc(), StockTransaction.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/transactions/{transaction_id}", response_model=TransactionRead)
def get_transaction(
    transaction_id: str,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
) -> StockTransaction:
    return load_transaction(session, transaction_id)


@router.get("/movements", response_model=list[StockMovementRead])
def list_movements(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
    item_id: str | None = None,
    location_id: str | None = None,
    transaction_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[StockMovement]:
    query = select(StockMovement)
    if item_id:
        query = query.where(StockMovement.item_id == item_id)
    if location_id:
        query = query.where(StockMovement.location_id == location_id)
    if transaction_id:
        query = query.where(StockMovement.transaction_id == transaction_id)
    return list(
        session.scalars(
            query.order_by(StockMovement.created_at.desc(), StockMovement.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.post("/transactions", response_model=TransactionRead, status_code=201)
def create_stock_transaction(
    payload: StockPostRequest,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    _permission: User = Depends(require_permission("inventory:transact")),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StockTransaction:
    transaction = post_stock_transaction(
        session,
        transaction_type=payload.type,
        actor_id=user.id,
        movements=payload.movements,
        reason=payload.reason,
        reference=payload.reference,
        recipient=payload.recipient,
        idempotency_key=idempotency_key,
    )
    return load_transaction(session, transaction.id)


@router.post("/transfers", response_model=TransactionRead, status_code=201)
def transfer_stock(
    payload: TransferRequest,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    _permission: User = Depends(require_permission("inventory:transact")),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StockTransaction:
    transaction = post_transfer(
        session,
        actor_id=user.id,
        item_id=payload.item_id,
        source_location_id=payload.source_location_id,
        destination_location_id=payload.destination_location_id,
        quantity=payload.quantity,
        reason=payload.reason,
        reference=payload.reference,
        idempotency_key=idempotency_key,
    )
    return load_transaction(session, transaction.id)
