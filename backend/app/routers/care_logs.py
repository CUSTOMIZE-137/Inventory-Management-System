from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.dependencies import current_user, require_permission
from app.models import (
    DailyCareLog,
    DailyCareLogLine,
    Department,
    Item,
    StockTransaction,
    User,
)
from app.schemas import DailyCareLogCreate, DailyCareLogRead, MovementInput
from app.services.inventory import normalize_item_quantity, post_stock_transaction

router = APIRouter(prefix="/api")


def _load_care_log(session: Session, log_id: str) -> dict[str, object]:
    care_log = session.scalar(
        select(DailyCareLog)
        .options(selectinload(DailyCareLog.lines))
        .where(DailyCareLog.id == log_id)
    )
    if care_log is None:
        raise HTTPException(status_code=404, detail="Care log not found")
    transaction_id = session.scalar(
        select(StockTransaction.id).where(
            StockTransaction.daily_care_log_id == care_log.id
        )
    )
    return {
        "id": care_log.id,
        "logged_by": care_log.logged_by,
        "department_id": care_log.department_id,
        "location_id": care_log.location_id,
        "activity_type": care_log.activity_type,
        "recipient": care_log.recipient,
        "notes": care_log.notes,
        "created_at": care_log.created_at,
        "transaction_id": transaction_id,
        "lines": care_log.lines,
    }


@router.post(
    "/care-logs",
    response_model=DailyCareLogRead,
    status_code=status.HTTP_201_CREATED,
)
def create_care_log(
    payload: DailyCareLogCreate,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    _permission: User = Depends(require_permission("inventory:transact")),
) -> dict[str, object]:
    if payload.department_id is not None and session.get(
        Department, payload.department_id
    ) is None:
        raise HTTPException(status_code=404, detail="Department not found")

    items: dict[str, Item] = {}
    normalized_lines = []
    for line in payload.lines:
        item = items.get(line.item_id)
        if item is None:
            item = session.get(Item, line.item_id)
            if item is None or not item.is_active:
                raise HTTPException(status_code=404, detail="Active item not found")
            items[item.id] = item
        if item.is_restricted or item.requires_approval:
            raise HTTPException(
                status_code=403,
                detail="Restricted items must be issued through an approved requisition",
            )
        normalized_lines.append(
            (
                line,
                normalize_item_quantity(
                    session, item, line.quantity, line.uom_code
                ),
            )
        )

    care_log = DailyCareLog(
        logged_by=user.id,
        department_id=payload.department_id,
        location_id=payload.location_id,
        activity_type=payload.activity_type,
        recipient=payload.recipient,
        notes=payload.notes,
    )
    for line, normalized in normalized_lines:
        care_log.lines.append(
            DailyCareLogLine(
                **line.model_dump(),
                normalized_quantity=normalized,
            )
        )
    session.add(care_log)
    session.flush()
    post_stock_transaction(
        session,
        transaction_type="issue",
        transaction_subtype="daily_dispense",
        daily_care_log_id=care_log.id,
        actor_id=user.id,
        movements=[
            MovementInput(
                item_id=line.item_id,
                location_id=payload.location_id,
                quantity_delta=-normalized,
            )
            for line, normalized in normalized_lines
        ],
        reason=f"Daily care dispense: {payload.activity_type}",
        recipient=payload.recipient,
    )
    return _load_care_log(session, care_log.id)


@router.get("/care-logs", response_model=list[DailyCareLogRead])
def list_care_logs(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
    location_id: str | None = None,
    activity_type: str | None = None,
    department_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    query = select(DailyCareLog.id)
    if location_id is not None:
        query = query.where(DailyCareLog.location_id == location_id)
    if activity_type is not None:
        query = query.where(DailyCareLog.activity_type == activity_type)
    if department_id is not None:
        query = query.where(DailyCareLog.department_id == department_id)
    ids = list(
        session.scalars(
            query.order_by(DailyCareLog.created_at.desc(), DailyCareLog.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return [_load_care_log(session, log_id) for log_id in ids]


@router.get("/care-logs/{log_id}", response_model=DailyCareLogRead)
def get_care_log(
    log_id: str,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
) -> dict[str, object]:
    return _load_care_log(session, log_id)
