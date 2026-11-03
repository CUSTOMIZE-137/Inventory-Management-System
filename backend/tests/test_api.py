from decimal import Decimal
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.cli import seed_roles_in_session
from app.models import (
    Base,
    Item,
    Location,
    StockBalance,
    StockMovement,
    StockTransaction,
    UnitOfMeasure,
    User,
    UserRole,
)
from app.reports import export_report, valuation_report
from app.routers.auth import (
    create_user,
    get_me,
    list_roles,
    list_users,
    login,
    update_user,
)
from app.routers.catalog import (
    create_category,
    create_department,
    create_item,
    create_location,
    create_unit,
    create_vendor,
    get_item,
    list_items,
    update_item,
    update_location,
    update_vendor,
)
from app.routers.inventory import (
    create_stock_transaction,
    get_transaction,
    list_movements,
    list_transactions,
    transfer_stock,
)
from app.routers.purchase_orders import (
    create_purchase_order,
    get_purchase_order,
    list_purchase_orders,
    receive_purchase_order,
)
from app.routers.requisitions import (
    approve_requisition,
    confirm_requisition,
    create_requisition,
    dispatch_requisition,
    get_requisition,
    list_requisitions,
)
from app.schemas import (
    ApprovalInput,
    CategoryCreate,
    DepartmentCreate,
    DispatchInput,
    ItemCreate,
    ItemUpdate,
    LocationCreate,
    LocationUpdate,
    LoginRequest,
    MovementInput,
    PurchaseOrderCreate,
    PurchaseOrderLineInput,
    PurchaseOrderReceive,
    PurchaseOrderReceiveLine,
    RequisitionCreate,
    RequisitionLineApprovalInput,
    RequisitionLineInput,
    StockPostRequest,
    TransferRequest,
    UnitOfMeasureCreate,
    UserCreate,
    UserUpdate,
    VendorCreate,
    VendorUpdate,
)
from app.security import hash_password, reset_login_rate_limits, token_subject


def _load_main(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv(
        "JWT_SECRET", "test-secret-with-at-least-thirty-two-characters"
    )
    from app import main

    return main


@pytest.fixture
def api_session(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv(
        "JWT_SECRET", "test-secret-with-at-least-thirty-two-characters"
    )
    reset_login_rate_limits()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        seed_roles_in_session(db)
        admin = User(
            id="admin-1",
            email="admin@example.test",
            name="System Admin",
            password_hash=hash_password("ValidAdminPassword123!"),
            is_active=True,
        )
        supervisor = User(
            id="sup-1",
            email="supervisor@example.test",
            name="Supervisor",
            password_hash=hash_password("ValidSupervisor123!"),
            is_active=True,
        )
        manager = User(
            id="mgr-1",
            email="manager@example.test",
            name="Store Manager",
            password_hash=hash_password("ValidManager123!"),
            is_active=True,
        )
        requester = User(
            id="req-user-1",
            email="requester@example.test",
            name="Requester",
            password_hash=hash_password("ValidRequester123!"),
            is_active=True,
        )
        db.add_all(
            [
                admin,
                supervisor,
                manager,
                requester,
                UserRole(user_id=admin.id, role_name="system_admin"),
                UserRole(user_id=supervisor.id, role_name="supervisor"),
                UserRole(user_id=manager.id, role_name="store_manager"),
                UserRole(user_id=requester.id, role_name="requester"),
                UnitOfMeasure(code="each", name="Each"),
                Item(
                    id="item-1",
                    sku="SKU-1",
                    name="Fiber Optic Patch Cord",
                    uom_code="each",
                    minimum_stock=Decimal("5"),
                    unit_cost=Decimal("12.5000"),
                ),
                Location(
                    id="loc-wh", name="Main Warehouse", location_type="warehouse"
                ),
            ]
        )
        db.flush()
        yield db
        db.rollback()
    engine.dispose()


def test_readiness_returns_ok_when_database_is_available(monkeypatch):
    main = _load_main(monkeypatch)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            return None

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(main, "engine", Engine())
    assert main.readiness() == {"status": "ok"}


def test_readiness_returns_503_when_database_is_unavailable(monkeypatch):
    main = _load_main(monkeypatch)

    class Engine:
        def connect(self):
            raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(main, "engine", Engine())
    with pytest.raises(HTTPException) as error:
        main.readiness()

    assert error.value.status_code == 503


def test_auth_login_me_and_rate_limiting(api_session: Session):
    admin = api_session.get(User, "admin-1")
    assert admin is not None

    token_resp = login(
        LoginRequest(
            email="ADMIN@EXAMPLE.TEST", password="ValidAdminPassword123!"
        ),
        request=None,
        session=api_session,
    )
    assert token_subject(token_resp.access_token) == "admin-1"

    me = get_me(user=admin, session=api_session)
    assert me.email == "admin@example.test"
    assert "system_admin" in me.roles
    assert "users:manage" in me.permissions

    # Rate limit after repeated attempts
    for _ in range(9):
        with pytest.raises(HTTPException) as exc:
            login(
                LoginRequest(
                    email="admin@example.test", password="wrong-password"
                ),
                request=None,
                session=api_session,
            )
        assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as rate_err:
        login(
            LoginRequest(email="admin@example.test", password="wrong-password"),
            request=None,
            session=api_session,
        )
    assert rate_err.value.status_code == 429


def test_user_and_role_management(api_session: Session):
    admin = api_session.get(User, "admin-1")
    roles = list_roles(session=api_session, _user=admin)
    assert any(r.name == "warehouse_clerk" for r in roles)

    dept = create_department(
        DepartmentCreate(name="Logistics"), session=api_session, _user=admin
    )
    created = create_user(
        UserCreate(
            email="clerk@example.test",
            name="Warehouse Clerk",
            password="StrongClerkPassword123!",
            department_id=dept["id"],
            roles=["warehouse_clerk"],
        ),
        session=api_session,
        _user=admin,
    )
    assert created.email == "clerk@example.test"
    assert created.roles == ["warehouse_clerk"]
    assert "inventory:transact" in created.permissions

    # Duplicate email returns 409
    with pytest.raises(HTTPException) as dup_err:
        create_user(
            UserCreate(
                email="clerk@example.test",
                name="Duplicate",
                password="StrongClerkPassword123!",
                roles=["warehouse_clerk"],
            ),
            session=api_session,
            _user=admin,
        )
    assert dup_err.value.status_code == 409

    # Update user roles and active status
    updated = update_user(
        created.id,
        UserUpdate(
            name="Senior Clerk",
            roles=["warehouse_clerk", "procurement"],
            is_active=False,
        ),
        session=api_session,
        _user=admin,
    )
    assert updated.name == "Senior Clerk"
    assert updated.is_active is False
    assert sorted(updated.roles) == ["procurement", "warehouse_clerk"]
    assert len(list_users(session=api_session, _user=admin, limit=10, offset=0)) >= 5


def test_catalog_validation_hierarchy_and_updates(api_session: Session):
    admin = api_session.get(User, "admin-1")

    # Duplicate SKU -> 409
    with pytest.raises(HTTPException) as sku_err:
        create_item(
            ItemCreate(sku="SKU-1", name="Duplicate", uom_code="each"),
            session=api_session,
            _user=admin,
        )
    assert sku_err.value.status_code == 409

    # Missing UOM -> 404
    with pytest.raises(HTTPException) as uom_err:
        create_item(
            ItemCreate(sku="SKU-99", name="Unknown UOM", uom_code="missing"),
            session=api_session,
            _user=admin,
        )
    assert uom_err.value.status_code == 404

    # Update item and filter inactive
    updated_item = update_item(
        "item-1",
        ItemUpdate(unit_cost=Decimal("15.0000"), is_active=False),
        session=api_session,
        _user=admin,
    )
    assert updated_item.unit_cost == Decimal("15.0000")
    assert updated_item.is_active is False
    assert (
        len(
            list_items(
                session=api_session,
                _user=admin,
                include_inactive=False,
                limit=100,
                offset=0,
            )
        )
        == 0
    )
    assert (
        len(
            list_items(
                session=api_session,
                _user=admin,
                include_inactive=True,
                limit=100,
                offset=0,
            )
        )
        == 1
    )
    update_item(
        "item-1", ItemUpdate(is_active=True), session=api_session, _user=admin
    )
    assert get_item("item-1", session=api_session, _user=admin).id == "item-1"

    # Location hierarchy validation: bin inside warehouse is valid; warehouse inside bin is rejected
    room = create_location(
        LocationCreate(
            name="Room 101", location_type="room", parent_id="loc-wh"
        ),
        session=api_session,
        _user=admin,
    )
    with pytest.raises(HTTPException) as loc_err:
        create_location(
            LocationCreate(
                name="Invalid Warehouse",
                location_type="warehouse",
                parent_id=room.id,
            ),
            session=api_session,
            _user=admin,
        )
    assert loc_err.value.status_code == 422

    with pytest.raises(HTTPException) as self_parent_err:
        update_location(
            room.id,
            LocationUpdate(parent_id=room.id),
            session=api_session,
            _user=admin,
        )
    assert self_parent_err.value.status_code == 422

    # Category cycle check
    cat_a = create_category(
        CategoryCreate(name="Networking"), session=api_session, _user=admin
    )
    with pytest.raises(HTTPException) as cat_err:
        create_category(
            CategoryCreate(name="Networking"), session=api_session, _user=admin
        )
    assert cat_err.value.status_code == 409
    assert cat_a["name"] == "Networking"

    unit = create_unit(
        UnitOfMeasureCreate(code="box", name="Box"),
        session=api_session,
        _user=admin,
    )
    assert unit["code"] == "box"


def test_purchase_order_consolidated_receive_and_read(api_session: Session):
    admin = api_session.get(User, "admin-1")
    vendor = create_vendor(
        VendorCreate(name="Acme Supplies", email="sales@acme.test"),
        session=api_session,
        _user=admin,
    )
    updated_vendor = update_vendor(
        vendor["id"],
        VendorUpdate(phone="+1-555-0100"),
        session=api_session,
        _user=admin,
    )
    assert updated_vendor.phone == "+1-555-0100"

    po = create_purchase_order(
        PurchaseOrderCreate(
            vendor_id=vendor["id"],
            reference="PO-2026-001",
            lines=[
                PurchaseOrderLineInput(
                    item_id="item-1",
                    ordered_qty=Decimal("10"),
                    unit_cost=Decimal("12.0000"),
                )
            ],
        ),
        session=api_session,
        user=admin,
    )
    assert len(po.lines) == 1
    po_line_id = po.lines[0].id

    # Duplicate line IDs in payload exceeding ordered_qty must fail BEFORE writing stock movements
    with pytest.raises(HTTPException) as over_err:
        receive_purchase_order(
            po.id,
            PurchaseOrderReceive(
                reason="Over-delivery attempt",
                lines=[
                    PurchaseOrderReceiveLine(
                        purchase_order_line_id=po_line_id,
                        location_id="loc-wh",
                        quantity=Decimal("6"),
                    ),
                    PurchaseOrderReceiveLine(
                        purchase_order_line_id=po_line_id,
                        location_id="loc-wh",
                        quantity=Decimal("6"),
                    ),
                ],
            ),
            session=api_session,
            user=admin,
        )
    assert over_err.value.status_code == 409
    assert api_session.scalar(select(StockTransaction.id)) is None

    # Valid split receive across two locations for the same PO line
    room = create_location(
        LocationCreate(
            name="Staging Room", location_type="room", parent_id="loc-wh"
        ),
        session=api_session,
        _user=admin,
    )
    tx = receive_purchase_order(
        po.id,
        PurchaseOrderReceive(
            reason="Partial split delivery",
            lines=[
                PurchaseOrderReceiveLine(
                    purchase_order_line_id=po_line_id,
                    location_id="loc-wh",
                    quantity=Decimal("4"),
                ),
                PurchaseOrderReceiveLine(
                    purchase_order_line_id=po_line_id,
                    location_id=room.id,
                    quantity=Decimal("6"),
                ),
            ],
        ),
        session=api_session,
        user=admin,
        idempotency_key="po-recv-1",
    )
    assert len(tx.movements) == 2
    po_after = get_purchase_order(po.id, session=api_session, _user=admin)
    assert po_after.status == "received"
    assert po_after.lines[0].received_qty == Decimal("10.000")
    assert (
        len(
            list_purchase_orders(
                session=api_session,
                _user=admin,
                status="received",
                limit=10,
                offset=0,
            )
        )
        == 1
    )


def test_partial_requisition_approval_ledger_queries_and_exports(
    api_session: Session,
):
    admin = api_session.get(User, "admin-1")
    supervisor = api_session.get(User, "sup-1")
    manager = api_session.get(User, "mgr-1")
    requester = api_session.get(User, "req-user-1")

    # Seed stock
    receipt_tx = create_stock_transaction(
        StockPostRequest(
            type="receipt",
            movements=[
                MovementInput(
                    item_id="item-1",
                    location_id="loc-wh",
                    quantity_delta=Decimal("20"),
                )
            ],
            reason="Initial stock",
            reference="INIT-1",
        ),
        session=api_session,
        user=admin,
        _permission=admin,
    )

    req = create_requisition(
        RequisitionCreate(
            reference="REQ-100",
            lines=[
                RequisitionLineInput(item_id="item-1", quantity=Decimal("10"))
            ],
        ),
        session=api_session,
        user=requester,
    )
    assert req.status == "pending_supervisor"

    # Supervisor approves partial quantity (6 of 10)
    sup_approved = approve_requisition(
        req.id,
        ApprovalInput(
            decision="approve",
            comment="Approved 6 units due to budget",
            line_approvals=[
                RequisitionLineApprovalInput(
                    item_id="item-1", quantity_approved=Decimal("6")
                )
            ],
        ),
        session=api_session,
        user=supervisor,
    )
    assert sup_approved.status == "pending_store_manager"
    assert sup_approved.lines[0].quantity_approved == Decimal("6.000")

    # Store manager approves without overriding supervisor's 6 units
    mgr_approved = approve_requisition(
        req.id,
        ApprovalInput(decision="approve", comment="Ready for dispatch"),
        session=api_session,
        user=manager,
    )
    assert mgr_approved.status == "approved_for_dispatch"
    assert mgr_approved.lines[0].quantity_approved == Decimal("6.000")
    assert len(mgr_approved.approvals) == 2

    # Dispatch and confirm
    dispatch_tx = dispatch_requisition(
        req.id,
        DispatchInput(location_id="loc-wh"),
        session=api_session,
        user=manager,
        _inventory_permission=manager,
    )
    assert dispatch_tx.movements[0].quantity_delta == Decimal("-6.000")

    confirmed = confirm_requisition(
        req.id, session=api_session, user=requester
    )
    assert confirmed.status == "completed"
    assert len(confirmed.approvals) == 3

    # Query requisitions and ledger
    reqs = list_requisitions(
        session=api_session,
        user=requester,
        status="completed",
        limit=10,
        offset=0,
    )
    assert len(reqs) == 1
    assert get_requisition(req.id, session=api_session, user=requester).id == req.id

    tx_list = list_transactions(
        session=api_session,
        _user=admin,
        item_id="item-1",
        limit=10,
        offset=0,
    )
    assert len(tx_list) == 2
    assert (
        get_transaction(receipt_tx.id, session=api_session, _user=admin).id
        == receipt_tx.id
    )

    movements = list_movements(
        session=api_session,
        _user=admin,
        item_id="item-1",
        location_id="loc-wh",
        limit=10,
        offset=0,
    )
    assert len(movements) == 2

    # Valuation report & CSV export
    val = valuation_report(session=api_session, _user=admin)
    assert val["total_value"] == Decimal("175.0000000")
    assert len(val["items"]) == 1

    csv_resp = export_report(
        "valuation", session=api_session, _user=admin, format="csv", days=30
    )
    assert csv_resp.media_type == "text/csv; charset=utf-8"
