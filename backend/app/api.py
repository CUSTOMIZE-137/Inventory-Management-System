from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.dependencies import current_user, require_permission
from app.models import (
    ApprovalAction,
    Category,
    Department,
    Item,
    Location,
    PurchaseOrder,
    PurchaseOrderLine,
    Requisition,
    RequisitionLine,
    RolePermission,
    StockBalance,
    StockTransaction,
    UnitOfMeasure,
    User,
    UserRole,
    Vendor,
)
from app.schemas import (
    ItemCreate,
    ItemRead,
    LocationCreate,
    LocationRead,
    LoginRequest,
    ApprovalInput,
    CategoryCreate,
    DepartmentCreate,
    DispatchInput,
    MovementInput,
    PurchaseOrderCreate,
    PurchaseOrderReceive,
    RequisitionCreate,
    StockLevelRead,
    StockPostRequest,
    TransactionRead,
    TransferRequest,
    TokenResponse,
    UnitOfMeasureCreate,
    VendorCreate,
)
from app.security import create_access_token, verify_password
from app.db import get_db
from app.services.inventory import post_stock_transaction, post_transfer


router = APIRouter(prefix="/api")


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(get_db)) -> TokenResponse:
    user = session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not user.is_active or not verify_password(
        payload.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/items", response_model=list[ItemRead])
def list_items(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Item]:
    return list(
        session.scalars(
            select(Item).where(Item.is_active.is_(True)).order_by(Item.name).limit(limit).offset(offset)
        )
    )


@router.post(
    "/items",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
)
def create_item(
    payload: ItemCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> Item:
    item = Item(**payload.model_dump())
    session.add(item)
    session.flush()
    return item


@router.post("/categories", status_code=201)
def create_category(
    payload: CategoryCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> dict[str, str | None]:
    category = Category(**payload.model_dump())
    session.add(category)
    session.flush()
    return {"id": category.id, "name": category.name, "parent_id": category.parent_id}


@router.get("/categories")
def list_categories(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
) -> list[dict[str, str | None]]:
    return [
        {"id": category.id, "name": category.name, "parent_id": category.parent_id}
        for category in session.scalars(select(Category).order_by(Category.name))
    ]


@router.post("/departments", status_code=201)
def create_department(
    payload: DepartmentCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> dict[str, str]:
    department = Department(name=payload.name)
    session.add(department)
    session.flush()
    return {"id": department.id, "name": department.name}


@router.get("/departments")
def list_departments(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
) -> list[dict[str, str]]:
    return [
        {"id": department.id, "name": department.name}
        for department in session.scalars(select(Department).order_by(Department.name))
    ]


@router.post("/units", status_code=201)
def create_unit(
    payload: UnitOfMeasureCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> dict[str, str]:
    unit = UnitOfMeasure(**payload.model_dump())
    session.add(unit)
    session.flush()
    return {"code": unit.code, "name": unit.name}


@router.get("/units")
def list_units(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
) -> list[dict[str, str]]:
    return [
        {"code": unit.code, "name": unit.name}
        for unit in session.scalars(select(UnitOfMeasure).order_by(UnitOfMeasure.name))
    ]


@router.get("/locations", response_model=list[LocationRead])
def list_locations(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
) -> list[Location]:
    return list(
        session.scalars(
            select(Location).where(Location.is_active.is_(True)).order_by(Location.name)
        )
    )


@router.post("/locations", response_model=LocationRead, status_code=201)
def create_location(
    payload: LocationCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> Location:
    location = Location(**payload.model_dump())
    session.add(location)
    session.flush()
    return location


@router.post("/vendors", status_code=201)
def create_vendor(
    payload: VendorCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> dict[str, object]:
    vendor = Vendor(**payload.model_dump())
    session.add(vendor)
    session.flush()
    return {
        "id": vendor.id,
        "name": vendor.name,
        "email": vendor.email,
        "phone": vendor.phone,
    }


@router.get("/vendors")
def list_vendors(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
) -> list[dict[str, str | None]]:
    return [
        {
            "id": vendor.id,
            "name": vendor.name,
            "email": vendor.email,
            "phone": vendor.phone,
        }
        for vendor in session.scalars(
            select(Vendor).where(Vendor.is_active.is_(True)).order_by(Vendor.name)
        )
    ]


@router.post("/purchase-orders", status_code=201)
def create_purchase_order(
    payload: PurchaseOrderCreate,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("catalog:write")),
) -> dict[str, object]:
    order = PurchaseOrder(
        vendor_id=payload.vendor_id,
        created_by=user.id,
        reference=payload.reference,
    )
    session.add(order)
    session.flush()
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
    return {"id": order.id, "reference": order.reference, "status": order.status}


@router.post("/purchase-orders/{order_id}/receive", response_model=TransactionRead)
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
    if order.status not in {"open", "partially_received"} and not (
        idempotency_key
        and session.scalar(
            select(StockTransaction.id).where(
                StockTransaction.idempotency_key == idempotency_key
            )
        )
    ):
        raise HTTPException(status_code=404, detail="Open purchase order not found")

    receipt_movements = []
    for received in payload.lines:
        line = session.scalar(
            select(PurchaseOrderLine)
            .where(
                PurchaseOrderLine.id == received.purchase_order_line_id,
                PurchaseOrderLine.purchase_order_id == order.id,
            )
            .with_for_update()
        )
        if line is None:
            raise HTTPException(status_code=404, detail="Purchase order line not found")
        receipt_movements.append(
            _movement_input(
                item_id=line.item_id,
                location_id=received.location_id,
                quantity_delta=received.quantity,
            )
        )

    prior_transaction_id = (
        session.scalar(
            select(StockTransaction.id).where(
                StockTransaction.idempotency_key == idempotency_key
            )
        )
        if idempotency_key
        else None
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
    if prior_transaction_id is not None:
        # A replay returns the original ledger entry without incrementing received_qty.
        return _load_transaction(session, transaction.id)

    for received in payload.lines:
        line = session.scalar(
            select(PurchaseOrderLine)
            .where(
                PurchaseOrderLine.id == received.purchase_order_line_id,
                PurchaseOrderLine.purchase_order_id == order.id,
            )
            .with_for_update()
        )
        if line is None:
            raise HTTPException(status_code=404, detail="Purchase order line not found")
        if line.received_qty + received.quantity > line.ordered_qty:
            raise HTTPException(status_code=409, detail="Receipt exceeds ordered quantity")
        line.received_qty += received.quantity
    all_lines = list(
        session.scalars(
            select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == order.id)
        )
    )
    order.status = (
        "received"
        if all(line.received_qty == line.ordered_qty for line in all_lines)
        else "partially_received"
    )
    session.flush()
    return _load_transaction(session, transaction.id)


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
    return _load_transaction(session, transaction.id)


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
    return _load_transaction(session, transaction.id)


@router.post("/requisitions", status_code=201)
def create_requisition(
    payload: RequisitionCreate,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("requisitions:request")),
) -> dict[str, object]:
    requisition = Requisition(
        requester_id=user.id,
        department_id=payload.department_id or user.department_id,
        reference=payload.reference,
        status="pending_supervisor",
    )
    session.add(requisition)
    session.flush()
    for line in payload.lines:
        session.add(
            RequisitionLine(
                requisition_id=requisition.id,
                item_id=line.item_id,
                quantity_requested=line.quantity,
                quantity_approved=Decimal("0"),
            )
        )
    session.flush()
    return _requisition_response(session, requisition)


@router.get("/requisitions/{requisition_id}")
def get_requisition(
    requisition_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    requisition = session.get(Requisition, requisition_id)
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if requisition.requester_id != user.id and not _has_permission(
        session, user.id, "requisitions:approve"
    ):
        raise HTTPException(status_code=403, detail="Permission denied")
    return _requisition_response(session, requisition)


@router.post("/requisitions/{requisition_id}/approvals")
def approve_requisition(
    requisition_id: str,
    payload: ApprovalInput,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("requisitions:approve")),
) -> dict[str, object]:
    requisition = session.scalar(
        select(Requisition)
        .where(Requisition.id == requisition_id)
        .with_for_update()
    )
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if requisition.requester_id == user.id:
        raise HTTPException(status_code=403, detail="Requesters cannot approve their own request")
    if requisition.status == "pending_supervisor":
        stage, next_status = "supervisor_review", "pending_store_manager"
    elif requisition.status == "pending_store_manager":
        stage, next_status = "store_manager_review", "approved_for_dispatch"
    else:
        raise HTTPException(status_code=409, detail="Requisition is not awaiting approval")
    if not _has_role(session, user.id, "system_admin"):
        required_role = "supervisor" if stage == "supervisor_review" else "store_manager"
        if not _has_role(session, user.id, required_role):
            raise HTTPException(status_code=403, detail="Wrong role for this approval stage")
    if stage == "store_manager_review" and session.scalar(
        select(ApprovalAction.actor_id).where(
            ApprovalAction.requisition_id == requisition.id,
            ApprovalAction.stage == "supervisor_review",
        )
    ) == user.id:
        raise HTTPException(
            status_code=403, detail="A different user must approve the second stage"
        )

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
        if next_status == "approved_for_dispatch":
            lines = session.scalars(
                select(RequisitionLine).where(
                    RequisitionLine.requisition_id == requisition.id
                )
            )
            for line in lines:
                line.quantity_approved = line.quantity_requested
    session.flush()
    return _requisition_response(session, requisition)


@router.post("/requisitions/{requisition_id}/dispatch", response_model=TransactionRead)
def dispatch_requisition(
    requisition_id: str,
    payload: DispatchInput,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("requisitions:dispatch")),
    _inventory_permission: User = Depends(require_permission("inventory:transact")),
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
        raise HTTPException(status_code=409, detail="Requisition is not ready for dispatch")
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
            _movement_input(
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
        return _load_transaction(session, transaction.id)
    requisition.status = "pending_confirmation"
    session.flush()
    return _load_transaction(session, transaction.id)


@router.post("/requisitions/{requisition_id}/confirm")
def confirm_requisition(
    requisition_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(require_permission("requisitions:confirm")),
) -> dict[str, object]:
    requisition = session.scalar(
        select(Requisition)
        .where(Requisition.id == requisition_id)
        .with_for_update()
    )
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if requisition.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Only the requester can confirm receipt")
    if requisition.status != "pending_confirmation":
        raise HTTPException(status_code=409, detail="Requisition is not awaiting confirmation")
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


def _load_transaction(session: Session, transaction_id: str) -> StockTransaction:
    transaction = session.scalar(
        select(StockTransaction)
        .options(selectinload(StockTransaction.movements))
        .where(StockTransaction.id == transaction_id)
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


def _movement_input(
    *, item_id: str, location_id: str, quantity_delta: Decimal
) -> MovementInput:
    return MovementInput(
        item_id=item_id, location_id=location_id, quantity_delta=quantity_delta
    )


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


def _requisition_response(session: Session, requisition: Requisition) -> dict[str, object]:
    lines = list(
        session.scalars(
            select(RequisitionLine).where(
                RequisitionLine.requisition_id == requisition.id
            )
        )
    )
    return {
        "id": requisition.id,
        "reference": requisition.reference,
        "status": requisition.status,
        "requester_id": requisition.requester_id,
        "lines": [
            {
                "item_id": line.item_id,
                "quantity_requested": line.quantity_requested,
                "quantity_approved": line.quantity_approved,
            }
            for line in lines
        ],
    }





