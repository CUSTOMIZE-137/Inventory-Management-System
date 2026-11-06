from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.dependencies import current_user, require_permission
from app.models import (
    Item,
    Location,
    StockBalance,
    StockTakeLine,
    StockTakeSession,
    User,
)
from app.schemas import (
    MovementInput,
    StockTakeSessionCreate,
    StockTakeSessionRead,
)
from app.services.inventory import post_stock_transaction

router = APIRouter(prefix="/api")


def _load_stock_take(
    session: Session, stock_take_id: str, *, lock: bool = False
) -> StockTakeSession:
    query = (
        select(StockTakeSession)
        .options(selectinload(StockTakeSession.lines))
        .where(StockTakeSession.id == stock_take_id)
    )
    if lock:
        query = query.with_for_update()
    stock_take = session.scalar(query)
    if stock_take is None:
        raise HTTPException(status_code=404, detail="Stock take session not found")
    return stock_take


@router.post(
    "/stock-takes",
    response_model=StockTakeSessionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_stock_take(
    payload: StockTakeSessionCreate,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    _permission: User = Depends(require_permission("inventory:transact")),
) -> StockTakeSession:
    # Capture the expected quantity when the count starts so the later
    # reconciliation remains tied to the physical count being reviewed.
    location = session.get(Location, payload.location_id)
    if location is None or not location.is_active:
        raise HTTPException(status_code=404, detail="Active location not found")
    item_ids = [line.item_id for line in payload.lines]
    if len(set(item_ids)) != len(item_ids):
        raise HTTPException(status_code=422, detail="A stock take may list each item once")
    items = {
        item.id: item
        for item in session.scalars(
            select(Item).where(Item.id.in_(item_ids), Item.is_active.is_(True))
        )
    }
    if set(items) != set(item_ids):
        raise HTTPException(status_code=404, detail="Active item not found")

    stock_take = StockTakeSession(
        title=payload.title,
        location_id=payload.location_id,
        conducted_by=user.id,
        notes=payload.notes,
    )
    for line in payload.lines:
        expected = session.scalar(
            select(StockBalance.quantity).where(
                StockBalance.item_id == line.item_id,
                StockBalance.location_id == payload.location_id,
            )
        )
        expected_qty = expected if expected is not None else Decimal("0")
        stock_take.lines.append(
            StockTakeLine(
                item_id=line.item_id,
                expected_qty=expected_qty,
                counted_qty=line.counted_qty,
                variance_qty=line.counted_qty - expected_qty,
                notes=line.notes,
            )
        )
    session.add(stock_take)
    session.flush()
    return stock_take


@router.post(
    "/stock-takes/{session_id}/finalize",
    response_model=StockTakeSessionRead,
)
def finalize_stock_take(
    session_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    _permission: User = Depends(require_permission("inventory:transact")),
) -> StockTakeSession:
    stock_take = _load_stock_take(session, session_id, lock=True)
    if stock_take.status != "in_progress":
        raise HTTPException(status_code=409, detail="Stock take session already finalized")
    variances = [
        line for line in stock_take.lines if line.variance_qty != Decimal("0")
    ]
    if variances:
        # Only the variance is posted; the original count and expected value
        # remain available on the stock-take record for audit.
        post_stock_transaction(
            session,
            transaction_type="adjustment",
            transaction_subtype="stock_take_adjustment",
            stock_take_session_id=stock_take.id,
            actor_id=user.id,
            movements=[
                MovementInput(
                    item_id=line.item_id,
                    location_id=stock_take.location_id,
                    quantity_delta=line.variance_qty,
                )
                for line in variances
            ],
            reason=f"Stock take reconciliation: {stock_take.title}",
        )
    stock_take.status = "posted"
    stock_take.posted_at = datetime.now(timezone.utc)
    session.flush()
    return _load_stock_take(session, stock_take.id)


@router.get("/stock-takes", response_model=list[StockTakeSessionRead])
def list_stock_takes(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
    location_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[StockTakeSession]:
    query = select(StockTakeSession).options(selectinload(StockTakeSession.lines))
    if location_id is not None:
        query = query.where(StockTakeSession.location_id == location_id)
    if status_filter is not None:
        query = query.where(StockTakeSession.status == status_filter)
    return list(
        session.scalars(
            query.order_by(StockTakeSession.created_at.desc(), StockTakeSession.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/stock-takes/{session_id}", response_model=StockTakeSessionRead)
def get_stock_take(
    session_id: str,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
) -> StockTakeSession:
    return _load_stock_take(session, session_id)
