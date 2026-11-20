from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db import get_db
from app.dependencies import current_user, require_roles
from app.models import Item, StockTransaction, TransactionType, User, UserRole
from app.schemas import (
    AuditSummaryRead,
    TransactionCreate,
    TransactionRead,
    TransactionSyncItemResult,
    TransactionSyncRequest,
    TransactionSyncResponse,
)
from app.services.inventory import record_stock_transaction, serialize_transaction

router = APIRouter(prefix="/api/v1", tags=["transactions"])


@router.post(
    "/transactions",
    response_model=TransactionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_transaction(
    payload: TransactionCreate,
    response: Response,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> TransactionRead:
    """Record a single stock movement atomically.

    Returns 201 Created for a new ledger entry, or 200 OK when an existing
    idempotency_key is replayed without re-applying the balance change.
    """
    tx, is_replayed = await record_stock_transaction(session, user=user, payload=payload)
    if is_replayed:
        response.status_code = status.HTTP_200_OK
    return tx


@router.post("/transactions/sync", response_model=TransactionSyncResponse)
async def sync_transactions(
    payload: TransactionSyncRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> TransactionSyncResponse:
    """Replay a chronological batch of offline-queued transactions.

    Each entry is isolated: validation or insufficient-stock failures mark only that
    item as 'rejected' so subsequent valid transactions in the batch still commit.
    """
    results: list[TransactionSyncItemResult] = []
    created_count = 0
    replayed_count = 0
    rejected_count = 0

    for item_payload in payload.transactions:
        try:
            tx, is_replayed = await record_stock_transaction(
                session, user=user, payload=item_payload
            )
            if is_replayed:
                replayed_count += 1
                results.append(
                    TransactionSyncItemResult(
                        idempotency_key=item_payload.idempotency_key,
                        status="replayed",
                        transaction=tx,
                    )
                )
            else:
                created_count += 1
                results.append(
                    TransactionSyncItemResult(
                        idempotency_key=item_payload.idempotency_key,
                        status="created",
                        transaction=tx,
                    )
                )
        except HTTPException as exc:
            rejected_count += 1
            results.append(
                TransactionSyncItemResult(
                    idempotency_key=item_payload.idempotency_key,
                    status="rejected",
                    error=str(exc.detail),
                )
            )

    return TransactionSyncResponse(
        results=results,
        created_count=created_count,
        replayed_count=replayed_count,
        rejected_count=rejected_count,
    )


@router.get("/transactions", response_model=list[TransactionRead])
async def list_transactions(
    item_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    transaction_type: TransactionType | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> list[TransactionRead]:
    stmt = select(StockTransaction).options(
        joinedload(StockTransaction.item), joinedload(StockTransaction.user)
    )

    if user.role == UserRole.REQUESTOR:
        # Requestors are strictly scoped to their own personal take-out history.
        stmt = stmt.where(StockTransaction.user_id == user.id)
    elif user_id is not None:
        stmt = stmt.where(StockTransaction.user_id == user_id)

    if item_id is not None:
        stmt = stmt.where(StockTransaction.item_id == item_id)
    if transaction_type is not None:
        stmt = stmt.where(StockTransaction.transaction_type == transaction_type)

    stmt = (
        stmt.order_by(StockTransaction.created_at.desc(), StockTransaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(await session.scalars(stmt))
    return [serialize_transaction(tx) for tx in rows]


@router.get("/audit/summary", response_model=AuditSummaryRead)
async def audit_summary(
    recent_limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_roles(UserRole.ADMIN)),
) -> AuditSummaryRead:
    total_users = int((await session.scalar(select(func.count()).select_from(User))) or 0)
    active_users = int(
        (
            await session.scalar(
                select(func.count()).select_from(User).where(User.is_active.is_(True))
            )
        )
        or 0
    )
    role_rows = (
        await session.execute(select(User.role, func.count(User.id)).group_by(User.role))
    ).all()
    users_by_role = {
        (row[0].value if isinstance(row[0], UserRole) else str(row[0])): int(row[1])
        for row in role_rows
    }

    total_items = int((await session.scalar(select(func.count()).select_from(Item))) or 0)
    low_stock_items = int(
        (
            await session.scalar(
                select(func.count())
                .select_from(Item)
                .where(Item.current_stock <= Item.reorder_level)
            )
        )
        or 0
    )
    total_stock_units = Decimal(
        str(
            (await session.scalar(select(func.coalesce(func.sum(Item.current_stock), 0))))
            or 0
        )
    )
    total_transactions = int(
        (await session.scalar(select(func.count()).select_from(StockTransaction))) or 0
    )

    recent_rows = list(
        await session.scalars(
            select(StockTransaction)
            .options(joinedload(StockTransaction.item), joinedload(StockTransaction.user))
            .order_by(StockTransaction.created_at.desc(), StockTransaction.id.desc())
            .limit(recent_limit)
        )
    )

    return AuditSummaryRead(
        total_users=total_users,
        active_users=active_users,
        users_by_role=users_by_role,
        total_items=total_items,
        low_stock_items=low_stock_items,
        total_stock_units=total_stock_units,
        total_transactions=total_transactions,
        recent_transactions=[serialize_transaction(tx) for tx in recent_rows],
    )
