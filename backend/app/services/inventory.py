import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Item, StockTransaction, TransactionType, User, UserRole, utc_now
from app.schemas import TransactionCreate, TransactionRead

# Per-item asyncio locks used only on SQLite (which ignores SELECT ... FOR UPDATE)
# to prevent concurrent coroutines from racing on the same item's stock balance.
_sqlite_item_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def _sqlite_write_guard(session: AsyncSession, item_id: str):
    """Serialize per-item stock mutations in-process when running against SQLite.

    PostgreSQL enforces row-level mutual exclusion natively via SELECT ... FOR UPDATE,
    so this guard is a no-op on non-SQLite backends.
    """
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name == "sqlite":
        lock = _sqlite_item_locks.setdefault(item_id, asyncio.Lock())
        async with lock:
            yield
    else:
        yield


def _safe_uuid(raw: str) -> UUID:
    """Parse a stored idempotency_key as UUID, falling back to deterministic uuid5 for legacy rows."""
    try:
        return UUID(raw)
    except ValueError:
        return uuid5(NAMESPACE_URL, raw)


def serialize_transaction(tx: StockTransaction) -> TransactionRead:
    return TransactionRead(
        id=tx.id,
        idempotency_key=_safe_uuid(tx.idempotency_key),
        item_id=tx.item_id,
        item_sku=tx.item.sku,
        item_name=tx.item.name,
        item_unit=tx.item.unit,
        user_id=tx.user_id,
        user_full_name=tx.user.full_name,
        user_email=tx.user.email,
        user_role=tx.user.role,
        transaction_type=tx.transaction_type,
        stock_source=tx.stock_source,
        quantity=tx.quantity,
        previous_stock=tx.previous_stock,
        remaining_stock=tx.remaining_stock,
        notes=tx.notes,
        created_at=tx.created_at,
    )


def _format_qty(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _enforce_transaction_rbac(user: User, transaction_type: TransactionType) -> None:
    """Enforce strict separation of duties on stock ledger mutations.

    - STORE_MANAGER exclusively records STOCK_IN and physical-count ADJUSTMENT entries.
    - REQUESTOR exclusively records STOCK_OUT take-outs.
    - ADMIN is restricted to read-only oversight and cannot mutate stock balances directly.
    """
    if transaction_type in {TransactionType.STOCK_IN, TransactionType.ADJUSTMENT}:
        if user.role != UserRole.STORE_MANAGER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Store Managers can record stock intake or count adjustments",
            )
    elif transaction_type == TransactionType.STOCK_OUT:
        if user.role != UserRole.REQUESTOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Requestors can record stock take-outs",
            )


async def _load_transaction_by_key(
    session: AsyncSession, idempotency_key: str
) -> StockTransaction | None:
    stmt = (
        select(StockTransaction)
        .options(joinedload(StockTransaction.item), joinedload(StockTransaction.user))
        .where(StockTransaction.idempotency_key == idempotency_key)
    )
    return await session.scalar(stmt)


async def record_stock_transaction(
    session: AsyncSession,
    *,
    user: User,
    payload: TransactionCreate,
) -> tuple[TransactionRead, bool]:
    """Atomically lock the item row, validate stock, update balance, and insert ledger row.

    Returns (transaction, is_replayed). Duplicate idempotency_key replays return
    the original transaction without recalculating stock twice.
    """
    _enforce_transaction_rbac(user, payload.transaction_type)
    key_str = str(payload.idempotency_key)

    # Fast-path idempotency check before acquiring any row lock.
    existing = await _load_transaction_by_key(session, key_str)
    if existing is not None:
        return serialize_transaction(existing), True

    async with _sqlite_write_guard(session, payload.item_id):
        # Re-check idempotency inside the lock to handle concurrent duplicate retries cleanly.
        existing = await _load_transaction_by_key(session, key_str)
        if existing is not None:
            return serialize_transaction(existing), True

        # Acquire row-level lock (SELECT ... FOR UPDATE) on the item row inside this ACID transaction.
        # populate_existing=True forces SQLAlchemy to refresh the ORM instance from the DB rather
        # than serving a stale cached balance from the session identity map.
        item = await session.scalar(
            select(Item)
            .where(Item.id == payload.item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found",
            )

        previous_stock = Decimal(str(item.current_stock))

        if payload.transaction_type == TransactionType.STOCK_IN:
            assert payload.quantity is not None
            quantity = Decimal(str(payload.quantity))
            remaining_stock = previous_stock + quantity
        elif payload.transaction_type == TransactionType.STOCK_OUT:
            assert payload.quantity is not None
            quantity = Decimal(str(payload.quantity))
            if previous_stock < quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Insufficient stock for {item.name} ({item.sku}): "
                        f"requested {_format_qty(quantity)} {item.unit}, "
                        f"only {_format_qty(previous_stock)} {item.unit} available"
                    ),
                )
            remaining_stock = previous_stock - quantity
        else:
            # Verified stock count correction (ADJUSTMENT)
            assert payload.counted_stock is not None
            target_stock = Decimal(str(payload.counted_stock))
            if target_stock == previous_stock:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Verified count ({_format_qty(target_stock)} {item.unit}) "
                        f"already matches current stock for {item.sku}"
                    ),
                )
            quantity = abs(target_stock - previous_stock)
            remaining_stock = target_stock

        cleaned_notes = payload.notes.strip() if payload.notes and payload.notes.strip() else None

        tx = StockTransaction(
            idempotency_key=key_str,
            item_id=item.id,
            user_id=user.id,
            transaction_type=payload.transaction_type,
            stock_source=payload.stock_source,
            quantity=quantity,
            previous_stock=previous_stock,
            remaining_stock=remaining_stock,
            notes=cleaned_notes,
        )

        try:
            # Use a SAVEPOINT (begin_nested) so an IntegrityError rolls back only this item's
            # mutation without aborting preceding items in a /transactions/sync batch.
            async with session.begin_nested():
                item.current_stock = remaining_stock
                item.updated_at = utc_now()
                session.add(tx)
                await session.flush()
        except IntegrityError as exc:
            # Handle race on unique idempotency_key cleanly without double-applying stock.
            replayed = await _load_transaction_by_key(session, key_str)
            if replayed is not None:
                return serialize_transaction(replayed), True
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Stock transaction violated inventory constraints",
            ) from exc

        loaded = await _load_transaction_by_key(session, key_str)
        if loaded is None:
            raise RuntimeError("Failed to reload recorded stock transaction")
        return serialize_transaction(loaded), False
