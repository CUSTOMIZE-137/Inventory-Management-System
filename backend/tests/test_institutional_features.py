import asyncio
from copy import copy
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import cli
from app.models import (
    Base,
    Category,
    DailyCareLog,
    DonationBatch,
    Item,
    Location,
    StockBalance,
    StockTransaction,
    UnitConversion,
    UnitOfMeasure,
    User,
)
from app.reports import export_report_by_type, monthly_ledger_report
from app.routers.care_logs import create_care_log
from app.routers.donations import create_donation, donation_monthly_summary
from app.routers.stock_take import create_stock_take, finalize_stock_take
from app.schemas import (
    DailyCareLogCreate,
    DailyCareLogLineInput,
    DonationBatchCreate,
    DonationBatchLineInput,
    MovementInput,
    StockTakeLineInput,
    StockTakeSessionCreate,
)
from app.services.inventory import normalize_item_quantity, post_stock_transaction


@pytest.fixture
def feature_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        user = User(
            id="feature-user",
            email="feature@example.test",
            name="Feature User",
            password_hash="not-used",
        )
        item = Item(
            id="feature-item",
            sku="FEATURE-1",
            name="Feature Item",
            uom_code="each",
        )
        location = Location(
            id="feature-location",
            name="Feature Warehouse",
            location_type="warehouse",
        )
        session.add_all(
            [
                user,
                item,
                location,
                UnitOfMeasure(code="each", name="Each"),
                UnitOfMeasure(code="box", name="Box"),
            ]
        )
        session.flush()
        yield session, user, item, location
    engine.dispose()


def test_unit_conversion_normalizes_forward_and_inverse(feature_session):
    session, _user, item, _location = feature_session
    session.add(
        UnitConversion(
            item_id=item.id,
            from_uom="box",
            to_uom="each",
            multiplier=Decimal("48"),
        )
    )
    session.flush()

    assert normalize_item_quantity(
        session, item, Decimal("1.5"), "box"
    ) == Decimal("72.000")
    assert normalize_item_quantity(
        session, item, Decimal("24"), "each"
    ) == Decimal("24.000")
    assert normalize_item_quantity(
        session, item, Decimal("1"), "each"
    ) == Decimal("1.000")
    session.delete(
        session.scalar(
            select(UnitConversion).where(
                UnitConversion.item_id == item.id,
                UnitConversion.from_uom == "box",
            )
        )
    )
    session.add(
        UnitConversion(
            item_id=item.id,
            from_uom="each",
            to_uom="box",
            multiplier=Decimal("48"),
        )
    )
    session.flush()
    assert normalize_item_quantity(
        session, item, Decimal("24"), "box"
    ) == Decimal("0.500")


def test_donation_is_idempotent_and_updates_stock_once(feature_session):
    session, user, item, location = feature_session
    payload = DonationBatchCreate(
        reference="DON-TEST-1",
        lines=[
            DonationBatchLineInput(
                item_id=item.id,
                location_id=location.id,
                quantity=Decimal("2"),
                uom_code="each",
            )
        ],
    )

    first = create_donation(
        payload,
        session=session,
        user=user,
        _permission=user,
        idempotency_key="donation-test-key",
    )
    replay = create_donation(
        payload,
        session=session,
        user=user,
        _permission=user,
        idempotency_key="donation-test-key",
    )

    assert first["id"] == replay["id"]
    assert first["transaction_id"] == replay["transaction_id"]
    assert session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.item_id == item.id,
            StockBalance.location_id == location.id,
        )
    ) == Decimal("2.000")
    assert session.scalar(select(func.count()).select_from(DonationBatch)) == 1
    transaction = session.get(StockTransaction, first["transaction_id"])
    assert transaction is not None
    assert transaction.transaction_type == "receipt"
    assert transaction.transaction_subtype == "donation_receipt"


def test_donation_monthly_summary_groups_by_year_and_normalizes_units(
    feature_session,
):
    session, user, item, location = feature_session
    session.add(Category(id="food-category", name="Foods"))
    item.category_id = "food-category"
    session.add(
        UnitConversion(
            item_id=item.id,
            from_uom="box",
            to_uom="each",
            multiplier=Decimal("12"),
        )
    )
    session.flush()

    for reference, quantity, uom_code, received_date in (
        ("DON-JAN", Decimal("2"), "each", datetime(2026, 1, 15, tzinfo=timezone.utc)),
        ("DON-FEB", Decimal("3"), "box", datetime(2026, 2, 15, tzinfo=timezone.utc)),
        ("DON-OLD", Decimal("5"), "each", datetime(2025, 12, 31, tzinfo=timezone.utc)),
    ):
        donation = create_donation(
            DonationBatchCreate(
                reference=reference,
                lines=[
                    DonationBatchLineInput(
                        item_id=item.id,
                        location_id=location.id,
                        quantity=quantity,
                        uom_code=uom_code,
                    )
                ],
            ),
            session=session,
            user=user,
            _permission=user,
        )
        session.get(DonationBatch, donation["id"]).received_date = received_date

    report = donation_monthly_summary(year=2026, session=session, _user=user)
    row = next(entry for entry in report["rows"] if entry["item_id"] == item.id)

    assert row["category_id"] == "food-category"
    assert row["monthly_quantities"][0] == Decimal("2.000")
    assert row["monthly_quantities"][1] == Decimal("36.000")
    assert row["monthly_quantities"][11] == Decimal("0.000")
    assert row["total_quantity"] == Decimal("38.000")


def test_care_log_posts_normalized_issue_and_rejects_restricted_item(feature_session):
    session, user, item, location = feature_session
    post_stock_transaction(
        session,
        transaction_type="receipt",
        actor_id=user.id,
        movements=[
            MovementInput(
                item_id=item.id,
                location_id=location.id,
                quantity_delta=Decimal("10"),
            )
        ],
        reason="Test opening stock",
    )
    session.add(
        UnitConversion(
            item_id=item.id,
            from_uom="box",
            to_uom="each",
            multiplier=Decimal("4"),
        )
    )
    session.flush()
    payload = DailyCareLogCreate(
        location_id=location.id,
        activity_type="kitchen_meal",
        recipient="Kitchen - Lunch",
        lines=[
            DailyCareLogLineInput(
                item_id=item.id,
                quantity=Decimal("1"),
                uom_code="box",
            )
        ],
    )

    created = create_care_log(
        payload, session=session, user=user, _permission=user
    )
    assert created["transaction_id"]
    assert created["lines"][0].normalized_quantity == Decimal("4.000")
    assert session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.item_id == item.id,
            StockBalance.location_id == location.id,
        )
    ) == Decimal("6.000")
    transaction = session.get(StockTransaction, created["transaction_id"])
    assert transaction is not None
    assert transaction.transaction_subtype == "daily_dispense"
    assert transaction.daily_care_log_id == created["id"]
    assert session.scalar(select(func.count()).select_from(DailyCareLog)) == 1

    item.is_restricted = True
    with pytest.raises(HTTPException) as error:
        create_care_log(
            payload, session=session, user=user, _permission=user
        )
    assert error.value.status_code == 403


def test_stock_take_finalize_posts_variance_and_cannot_repeat(feature_session):
    session, user, item, location = feature_session
    post_stock_transaction(
        session,
        transaction_type="receipt",
        actor_id=user.id,
        movements=[
            MovementInput(
                item_id=item.id,
                location_id=location.id,
                quantity_delta=Decimal("8"),
            )
        ],
        reason="Test opening stock",
    )
    payload = StockTakeSessionCreate(
        title="Quarterly count",
        location_id=location.id,
        lines=[StockTakeLineInput(item_id=item.id, counted_qty=Decimal("5"))],
    )
    stock_take = create_stock_take(
        payload, session=session, user=user, _permission=user
    )
    finalized = finalize_stock_take(
        stock_take.id, session=session, user=user, _permission=user
    )

    assert finalized.status == "posted"
    assert finalized.lines[0].variance_qty == Decimal("-3.000")
    assert session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.item_id == item.id,
            StockBalance.location_id == location.id,
        )
    ) == Decimal("5.000")
    transaction = session.scalar(
        select(StockTransaction).where(
            StockTransaction.stock_take_session_id == stock_take.id
        )
    )
    assert transaction is not None
    assert transaction.transaction_subtype == "stock_take_adjustment"
    with pytest.raises(HTTPException) as error:
        finalize_stock_take(
            stock_take.id, session=session, user=user, _permission=user
        )
    assert error.value.status_code == 409


def test_monthly_ledger_classifies_specialized_transactions(feature_session):
    session, user, item, location = feature_session

    def post(
        transaction_type: str,
        quantity: str,
        subtype: str | None,
    ) -> StockTransaction:
        return post_stock_transaction(
            session,
            transaction_type=transaction_type,
            transaction_subtype=subtype,
            actor_id=user.id,
            movements=[
                MovementInput(
                    item_id=item.id,
                    location_id=location.id,
                    quantity_delta=Decimal(quantity),
                )
            ],
            reason="Ledger test",
        )

    opening = post("receipt", "10", "opening_balance")
    purchased = post("receipt", "4", "purchase_receipt")
    donated = post("receipt", "3", "donation_receipt")
    daily = post("issue", "-1", "daily_dispense")
    other_issue = post("issue", "-2", None)
    adjustment = post("adjustment", "2", None)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for transaction in (opening, purchased, donated, daily, other_issue, adjustment):
        transaction.created_at = period_start
    for transaction in (purchased, donated, daily, other_issue, adjustment):
        transaction.created_at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    session.flush()

    report = monthly_ledger_report(
        session=session,
        _user=user,
        year=2026,
        month=1,
        location_id=location.id,
    )
    row = report["rows"][0]
    assert row["opening_stock"] == Decimal("10.000")
    assert row["purchased_qty"] == Decimal("4.000")
    assert row["donated_qty"] == Decimal("3.000")
    assert row["daily_dispensed_qty"] == Decimal("1.000")
    assert row["other_issued_qty"] == Decimal("2.000")
    assert row["net_adjustments_and_transfers"] == Decimal("2.000")
    assert row["closing_stock"] == Decimal("16.000")
    response = export_report_by_type(
        report_type="monthly-ledger",
        session=session,
        _user=user,
        year=2026,
        month=1,
        location_id=location.id,
    )

    async def read_csv() -> str:
        return "".join([chunk async for chunk in response.body_iterator])

    csv_text = asyncio.run(read_csv())
    assert "opening_stock" in csv_text
    assert "FEATURE-1" in csv_text
    assert "TOTALS" in csv_text


def test_excel_catalog_import_is_repeatable_and_does_not_create_stock(
    tmp_path, monkeypatch
):
    from openpyxl import Workbook

    workbook_path = tmp_path / "catalog.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "APRIL2026"
    worksheet["A1"] = "April"
    worksheet["A2"] = "Items"
    worksheet["A3"] = "Foods"
    heading_font = copy(worksheet["A3"].font)
    heading_font.bold = True
    worksheet["A3"].font = heading_font
    worksheet["A4"] = "Beans"
    worksheet["B4"] = 10
    worksheet["A5"] = "beans"
    worksheet["B5"] = 5
    workbook.save(workbook_path)
    workbook.close()

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        cli,
        "SessionLocal",
        sessionmaker(bind=engine, expire_on_commit=False),
    )

    assert cli.import_excel_catalog(str(workbook_path), "APRIL2026") == (1, 0)
    assert cli.import_excel_catalog(str(workbook_path), "APRIL2026") == (0, 1)
    with Session(engine) as session:
        item = session.scalar(select(Item).where(Item.name == "Beans"))
        assert item is not None
        assert item.uom_code == "each"
        assert item.category_id is not None
        assert session.scalar(select(func.count()).select_from(StockBalance)) == 0
    engine.dispose()
