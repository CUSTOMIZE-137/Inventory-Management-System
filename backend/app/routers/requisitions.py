from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import current_user, require_permission
from app.models import (
    ApprovalAction,
    Department,
    Item,
    Requisition,
    RequisitionLine,
    RolePermission,
    StockTransaction,
    User,
    UserRole,
)
from app.routers.inventory import load_transaction
from app.schemas import (
    ApprovalActionRead,
    ApprovalInput,
    DispatchInput,
    MovementInput,
    RequisitionCreate,
    RequisitionLineRead,
    RequisitionRead,
    TransactionRead,
)
from app.services.inventory import post_stock_transaction

router = APIRouter(prefix="/api")


def _has_role(session: Session, user_id: str, role_name: str) -> bool:
    return (
        session.scalar(
            select(UserRole.user_id).where(
                UserRole.user_id == user_id,
                UserRole.role_name == role_name,
            )
        )
        is not None
    )


def _has_permission(session: Session, user_id: str, permission_name: str) -> bool:
    return (
        session.scalar(
            select(UserRole.user_id)
            .join(RolePermission, RolePermission.role_name == UserRole.role_name)
            .where(
                UserRole.user_id == user_id,
                RolePermission.permission_name == permission_name,
            )
        )
        is not None
    )


def _requisition_response(
    session: Session, requisition: Requisition
) -> RequisitionRead:
    lines = list(
        session.scalars(
            select(RequisitionLine)
            .where(RequisitionLine.requisition_id == requisition.id)
            .order_by(RequisitionLine.id)
        )
    )
    approvals = list(
        session.scalars(
            select(ApprovalAction)
            .where(ApprovalAction.requisition_id == requisition.id)
            .order_by(ApprovalAction.acted_at, ApprovalAction.id)
        )
    )
    return RequisitionRead(
        id=requisition.id,
        reference=requisition.reference,
        status=requisition.status,
        requester_id=requisition.requester_id,
        department_id=requisition.department_id,
        created_at=requisition.created_at,
        lines=[RequisitionLineRead.model_validate(line) for line in lines],
        approvals=[
            ApprovalActionRead.model_validate(action) for action in approvals
        ],
    )


@router.get("/requisitions", response_model=list[RequisitionRead])
def list_requisitions(
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    status: str | None = None,
    requester_id: str | None = None,
    department_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RequisitionRead]:
    can_view_all = _has_permission(
        session, user.id, "requisitions:approve"
    ) or _has_permission(session, user.id, "requisitions:dispatch")
    query = select(Requisition)
    if not can_view_all:
        query = query.where(Requisition.requester_id == user.id)
    elif requester_id:
        query = query.where(Requisition.requester_id == requester_id)
    if status:
        query = query.where(Requisition.status == status)
    if department_id:
        query = query.where(Requisition.department_id == department_id)
    requisitions = list(
        session.scalars(
            query.order_by(Requisition.created_at.desc(), Requisition.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return [_requisition_response(session, req) for req in requisitions]


@router.post("/requisitions", response_model=RequisitionRead, status_code=201)
def create_requisition(
    payload: RequisitionCreate,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("requisitions:request")),
) -> RequisitionRead:
    dept_id = payload.department_id or user.department_id
    if dept_id is not None and session.get(Department, dept_id) is None:
        raise HTTPException(status_code=404, detail="Department not found")
    if payload.reference is not None:
        if (
            session.scalar(
                select(Requisition.id).where(
                    Requisition.reference == payload.reference
                )
            )
            is not None
        ):
            raise HTTPException(
                status_code=409, detail="Requisition reference already exists"
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

    requisition = Requisition(
        requester_id=user.id,
        department_id=dept_id,
        reference=payload.reference,
        status="pending_supervisor",
    )
    session.add(requisition)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Requisition creation conflict"
        ) from error

    consolidated_lines: dict[str, Decimal] = {}
    for line in payload.lines:
        consolidated_lines[line.item_id] = (
            consolidated_lines.get(line.item_id, Decimal("0")) + line.quantity
        )
    for item_id, quantity in consolidated_lines.items():
        session.add(
            RequisitionLine(
                requisition_id=requisition.id,
                item_id=item_id,
                quantity_requested=quantity,
                quantity_approved=Decimal("0"),
            )
        )
    session.flush()
    return _requisition_response(session, requisition)


@router.get("/requisitions/{requisition_id}", response_model=RequisitionRead)
def get_requisition(
    requisition_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> RequisitionRead:
    requisition = session.get(Requisition, requisition_id)
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if (
        requisition.requester_id != user.id
        and not _has_permission(session, user.id, "requisitions:approve")
        and not _has_permission(session, user.id, "requisitions:dispatch")
    ):
        raise HTTPException(status_code=403, detail="Permission denied")
    return _requisition_response(session, requisition)


@router.post(
    "/requisitions/{requisition_id}/approvals", response_model=RequisitionRead
)
def approve_requisition(
    requisition_id: str,
    payload: ApprovalInput,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("requisitions:approve")),
) -> RequisitionRead:
    requisition = session.scalar(
        select(Requisition)
        .where(Requisition.id == requisition_id)
        .with_for_update()
    )
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if requisition.requester_id == user.id:
        raise HTTPException(
            status_code=403, detail="Requesters cannot approve their own request"
        )
    if requisition.status == "pending_supervisor":
        stage, next_status = "supervisor_review", "pending_store_manager"
    elif requisition.status == "pending_store_manager":
        stage, next_status = "store_manager_review", "approved_for_dispatch"
    else:
        raise HTTPException(
            status_code=409, detail="Requisition is not awaiting approval"
        )
    if not _has_role(session, user.id, "system_admin"):
        required_role = (
            "supervisor" if stage == "supervisor_review" else "store_manager"
        )
        if not _has_role(session, user.id, required_role):
            raise HTTPException(
                status_code=403, detail="Wrong role for this approval stage"
            )
    if (
        stage == "store_manager_review"
        and session.scalar(
            select(ApprovalAction.actor_id).where(
                ApprovalAction.requisition_id == requisition.id,
                ApprovalAction.stage == "supervisor_review",
            )
        )
        == user.id
    ):
        raise HTTPException(
            status_code=403,
            detail="A different user must approve the second stage",
        )

    lines = list(
        session.scalars(
            select(RequisitionLine)
            .where(RequisitionLine.requisition_id == requisition.id)
            .with_for_update()
        )
    )
    if payload.decision == "approve" and payload.line_approvals is not None:
        lines_by_item = {line.item_id: line for line in lines}
        overrides: dict[str, Decimal] = {}
        for item_approval in payload.line_approvals:
            if item_approval.item_id not in lines_by_item:
                raise HTTPException(
                    status_code=422,
                    detail="Line approval item is not on this requisition",
                )
            line = lines_by_item[item_approval.item_id]
            if item_approval.quantity_approved > line.quantity_requested:
                raise HTTPException(
                    status_code=422,
                    detail="Approved quantity cannot exceed requested quantity",
                )
            overrides[item_approval.item_id] = item_approval.quantity_approved
        for line in lines:
            line.quantity_approved = overrides.get(
                line.item_id, line.quantity_requested
            )
        if lines and all(line.quantity_approved == 0 for line in lines):
            raise HTTPException(
                status_code=422,
                detail="At least one requisition line must have a positive approved quantity",
            )
    elif payload.decision == "approve" and next_status == "approved_for_dispatch":
        for line in lines:
            if line.quantity_approved == 0:
                line.quantity_approved = line.quantity_requested

    session.add(
        ApprovalAction(
            requisition_id=requisition.id,
            stage=stage,
            actor_id=user.id,
            decision=payload.decision,
            comment=payload.comment,
        )
    )
    if payload.decision == "reject":
        requisition.status = "rejected"
    else:
        requisition.status = next_status
    session.flush()
    return _requisition_response(session, requisition)


@router.post(
    "/requisitions/{requisition_id}/dispatch", response_model=TransactionRead
)
def dispatch_requisition(
    requisition_id: str,
    payload: DispatchInput,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("requisitions:dispatch")),
    _inventory_permission: User = Depends(
        require_permission("inventory:transact")
    ),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StockTransaction:
    requisition = session.scalar(
        select(Requisition)
        .where(Requisition.id == requisition_id)
        .with_for_update()
    )
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    existing = (
        session.scalar(
            select(StockTransaction).where(
                StockTransaction.idempotency_key == idempotency_key,
                StockTransaction.requisition_id == requisition.id,
            )
        )
        if idempotency_key
        else None
    )
    is_retry = existing is not None
    if not is_retry and requisition.status != "approved_for_dispatch":
        raise HTTPException(
            status_code=409, detail="Requisition is not ready for dispatch"
        )
    lines = list(
        session.scalars(
            select(RequisitionLine).where(
                RequisitionLine.requisition_id == requisition.id
            )
        )
    )
    transaction = post_stock_transaction(
        session,
        transaction_type="issue",
        actor_id=user.id,
        movements=[
            MovementInput(
                item_id=line.item_id,
                location_id=payload.location_id,
                quantity_delta=-line.quantity_approved,
            )
            for line in lines
            if line.quantity_approved > 0
        ],
        reason="Dispatch for approved requisition",
        reference=requisition.reference,
        recipient=requisition.requester_id,
        requisition_id=requisition.id,
        idempotency_key=idempotency_key,
    )
    if is_retry:
        return load_transaction(session, transaction.id)
    requisition.status = "pending_confirmation"
    session.flush()
    return load_transaction(session, transaction.id)


@router.post(
    "/requisitions/{requisition_id}/confirm", response_model=RequisitionRead
)
def confirm_requisition(
    requisition_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("requisitions:confirm")),
) -> RequisitionRead:
    requisition = session.scalar(
        select(Requisition)
        .where(Requisition.id == requisition_id)
        .with_for_update()
    )
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if requisition.requester_id != user.id:
        raise HTTPException(
            status_code=403, detail="Only the requester can confirm receipt"
        )
    if requisition.status != "pending_confirmation":
        raise HTTPException(
            status_code=409, detail="Requisition is not awaiting confirmation"
        )
    requisition.status = "completed"
    session.add(
        ApprovalAction(
            requisition_id=requisition.id,
            stage="final_confirmation",
            actor_id=user.id,
            decision="confirmed",
        )
    )
    session.flush()
    return _requisition_response(session, requisition)
