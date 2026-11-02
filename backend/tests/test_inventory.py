from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Item,
    Location,
    Permission,
    Requisition,
    RequisitionLine,
    Role,
    StockBalance,
    StockMovement,
    StockTransaction,
    User,
    UserRole,
    UnitOfMeasure,
)
from app.schemas import MovementInput
from app.services.inventory import post_stock_transaction, post_transfer
from app.security import hash_password, verify_password


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all(
            [
                User(
                    id="user-1",
                    email="admin@example.test",
                    name="Admin",
                    password_hash=hash_password("test-password"),
                ),
                UnitOfMeasure(code="each", name="Each"),
                Item(id="item-1", sku="SKU-1", name="Cable", uom_code="each"),
                Location(id="loc-1", name="Store A", location_type="warehouse"),
                Location(id="loc-2", name="Store B", location_type="warehouse"),
                Role(name="system_admin"),
                Permission(name="inventory:transact"),
            ]
        )
        db.flush()
        yield db
        db.rollback()
    engine.dispose()


def test_receipt_issue_and_audit_snapshot(session: Session):
    received = post_stock_transaction(
        session,
        transaction_type="receipt",
        actor_id="user-1",
        movements=[
            MovementInput(
                item_id="item-1",
                location_id="loc-1",
                quantity_delta=Decimal("10"),
            )
        ],
        reason="Initial receipt",
    )
    issued = post_stock_transaction(
        session,
        transaction_type="issue",
        actor_id="user-1",
        movements=[
            MovementInput(
                item_id="item-1",
                location_id="loc-1",
                quantity_delta=Decimal("-3"),
            )
        ],
        reason="Issued to team",
        recipient="Engineering",
    )
    balance = session.get(StockBalance, ("item-1", "loc-1"))
    assert balance is not None and balance.quantity == Decimal("7.000")
    receipt_movement = session.scalar(
        select(StockMovement).where(StockMovement.transaction_id == received.id)
    )
    issue_movement = session.scalar(
        select(StockMovement).where(StockMovement.transaction_id == issued.id)
    )
    assert receipt_movement is not None
    assert (receipt_movement.quantity_before, receipt_movement.quantity_after) == (
        Decimal("0"),
        Decimal("10.000"),
    )
    assert issue_movement is not None
    assert (issue_movement.quantity_before, issue_movement.quantity_after) == (
        Decimal("10.000"),
        Decimal("7.000"),
    )


def test_insufficient_issue_fails_without_movement(session: Session):
    with pytest.raises(HTTPException) as error:
        post_stock_transaction(
            session,
            transaction_type="issue",
            actor_id="user-1",
            movements=[
                MovementInput(
                    item_id="item-1",
                    location_id="loc-1",
                    quantity_delta=Decimal("-1"),
                )
            ],
            reason="Issue",
        )
    assert error.value.status_code == 409
    assert session.scalar(select(StockTransaction.id)) is None


def test_transfer_is_atomic_and_idempotent(session: Session):
    post_stock_transaction(
        session,
        transaction_type="receipt",
        actor_id="user-1",
        movements=[
            MovementInput(
                item_id="item-1",
                location_id="loc-1",
                quantity_delta=Decimal("8"),
            )
        ],
        reason="Receipt",
    )
    transaction = post_transfer(
        session,
        actor_id="user-1",
        item_id="item-1",
        source_location_id="loc-1",
        destination_location_id="loc-2",
        quantity=Decimal("5"),
        reason="Relocation",
        idempotency_key="transfer-1",
    )
    replay = post_transfer(
        session,
        actor_id="user-1",
        item_id="item-1",
        source_location_id="loc-1",
        destination_location_id="loc-2",
        quantity=Decimal("5"),
        reason="Relocation",
        idempotency_key="transfer-1",
    )
    assert replay.id == transaction.id
    assert session.get(StockBalance, ("item-1", "loc-1")).quantity == Decimal("3.000")
    assert session.get(StockBalance, ("item-1", "loc-2")).quantity == Decimal("5.000")
    assert len(
        list(
            session.scalars(
                select(StockMovement).where(
                    StockMovement.transaction_id == transaction.id
                )
            )
        )
    ) == 2


def test_idempotency_key_rejects_different_payload(session: Session):
    post_stock_transaction(
        session,
        transaction_type="receipt",
        actor_id="user-1",
        movements=[
            MovementInput(
                item_id="item-1",
                location_id="loc-1",
                quantity_delta=Decimal("5"),
            )
        ],
        reason="First payload",
        idempotency_key="key-1",
    )
    with pytest.raises(HTTPException) as error:
        post_stock_transaction(
            session,
            transaction_type="receipt",
            actor_id="user-1",
            movements=[
                MovementInput(
                    item_id="item-1",
                    location_id="loc-1",
                    quantity_delta=Decimal("6"),
                )
            ],
            reason="Different payload",
            idempotency_key="key-1",
        )
    assert error.value.status_code == 409


def test_restricted_issue_requires_approved_requisition(session: Session):
    item = session.get(Item, "item-1")
    assert item is not None
    item.requires_approval = True
    post_stock_transaction(
        session,
        transaction_type="receipt",
        actor_id="user-1",
        movements=[
            MovementInput(
                item_id="item-1",
                location_id="loc-1",
                quantity_delta=Decimal("5"),
            )
        ],
        reason="Initial receipt",
    )
    with pytest.raises(HTTPException) as error:
        post_stock_transaction(
            session,
            transaction_type="issue",
            actor_id="user-1",
            movements=[
                MovementInput(
                    item_id="item-1",
                    location_id="loc-1",
                    quantity_delta=Decimal("-2"),
                )
            ],
            reason="Direct issue",
        )
    assert error.value.status_code == 403

    requisition = Requisition(
        id="req-1",
        requester_id="user-1",
        status="approved_for_dispatch",
    )
    session.add(requisition)
    session.add(
        RequisitionLine(
            id="req-line-1",
            requisition_id=requisition.id,
            item_id="item-1",
            quantity_requested=Decimal("2"),
            quantity_approved=Decimal("2"),
        )
    )
    session.flush()
    post_stock_transaction(
        session,
        transaction_type="issue",
        actor_id="user-1",
        movements=[
            MovementInput(
                item_id="item-1",
                location_id="loc-1",
                quantity_delta=Decimal("-2"),
            )
        ],
        reason="Approved dispatch",
        requisition_id=requisition.id,
    )
    balance = session.get(StockBalance, ("item-1", "loc-1"))
    assert balance is not None and balance.quantity == Decimal("3.000")


def test_dispatch_retry_returns_original_transaction(session: Session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-with-at-least-thirty-two-characters")
    from app.api import dispatch_requisition
    from app.schemas import DispatchInput

    post_stock_transaction(
        session,
        transaction_type="receipt",
        actor_id="user-1",
        movements=[
            MovementInput(
                item_id="item-1",
                location_id="loc-1",
                quantity_delta=Decimal("5"),
            )
        ],
        reason="Initial receipt",
    )
    requisition = Requisition(
        id="req-dispatch-retry",
        requester_id="user-1",
        status="approved_for_dispatch",
    )
    session.add(requisition)
    session.add(
        RequisitionLine(
            id="req-line-dispatch-retry",
            requisition_id=requisition.id,
            item_id="item-1",
            quantity_requested=Decimal("2"),
            quantity_approved=Decimal("2"),
        )
    )
    session.flush()

    first = dispatch_requisition(
        requisition.id,
        DispatchInput(location_id="loc-1"),
        session,
        session.get(User, "user-1"),
        session.get(User, "user-1"),
        "dispatch-retry-key",
    )
    replay = dispatch_requisition(
        requisition.id,
        DispatchInput(location_id="loc-1"),
        session,
        session.get(User, "user-1"),
        session.get(User, "user-1"),
        "dispatch-retry-key",
    )
    requisition.status = "completed"
    completed_replay = dispatch_requisition(
        requisition.id,
        DispatchInput(location_id="loc-1"),
        session,
        session.get(User, "user-1"),
        session.get(User, "user-1"),
        "dispatch-retry-key",
    )

    assert replay.id == first.id
    assert completed_replay.id == first.id
    assert session.get(StockBalance, ("item-1", "loc-1")).quantity == Decimal("3.000")


def test_requisition_stages_require_distinct_approvers(session: Session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-with-at-least-thirty-two-characters")
    from app.api import approve_requisition
    from app.schemas import ApprovalInput

    requester = User(
        id="requester-2",
        email="requester@example.test",
        name="Requester",
        password_hash=hash_password("another-test-password"),
    )
    session.add(requester)
    session.add(UserRole(user_id="user-1", role_name="system_admin"))
    requisition = Requisition(
        id="req-distinct-approvers",
        requester_id=requester.id,
        status="pending_supervisor",
    )
    session.add(requisition)
    session.flush()

    approve_requisition(
        requisition.id,
        ApprovalInput(decision="approve"),
        session,
        session.get(User, "user-1"),
    )
    with pytest.raises(HTTPException) as error:
        approve_requisition(
            requisition.id,
            ApprovalInput(decision="approve"),
            session,
            session.get(User, "user-1"),
        )

    assert error.value.status_code == 403


def test_stock_and_reports_support_pagination(session: Session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-with-at-least-thirty-two-characters")
    from app.api import get_stock
    from app.reports import _low_stock_rows, valuation_report

    item = session.get(Item, "item-1")
    assert item is not None
    item.minimum_stock = Decimal("10")
    item.unit_cost = Decimal("2.5000")
    second_item = Item(
        id="item-2",
        sku="SKU-2",
        name="Cable two",
        uom_code="each",
        minimum_stock=Decimal("10"),
    )
    session.add_all(
        [
            second_item,
            StockBalance(
                item_id="item-1", location_id="loc-1", quantity=Decimal("2")
            ),
            StockBalance(
                item_id="item-1", location_id="loc-2", quantity=Decimal("3")
            ),
        ]
    )
    session.flush()

    first_stock_page = get_stock(
        session=session, _user=session.get(User, "user-1"), limit=1, offset=0
    )
    second_stock_page = get_stock(
        session=session, _user=session.get(User, "user-1"), limit=1, offset=1
    )
    first_report_page = _low_stock_rows(session, limit=1, offset=0)
    second_report_page = _low_stock_rows(session, limit=1, offset=1)

    assert len(first_stock_page) == len(second_stock_page) == 1
    assert first_stock_page[0].location_id != second_stock_page[0].location_id
    assert len(first_report_page) == len(second_report_page) == 1
    assert first_report_page[0]["item_id"] != second_report_page[0]["item_id"]
    valuation = valuation_report(session=session)
    assert valuation["valuation_method"] == "standard_cost"
    assert valuation["total_value"] == Decimal("12.500000")


def test_password_hash_does_not_store_plaintext():
    hashed = hash_password("strong-test-password")
    assert hashed != "strong-test-password"
    assert verify_password("strong-test-password", hashed)
    assert not verify_password("wrong-password", hashed)
