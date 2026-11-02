import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models import Base, Item, Location, StockBalance, StockTransaction, UnitOfMeasure, User
from app.schemas import MovementInput
from app.services.inventory import post_stock_transaction


pytestmark = pytest.mark.postgresql
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture
def postgres_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")

    engine = create_engine(TEST_DATABASE_URL, pool_size=5)
    schema_name = f"inventory_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    isolated_engine = engine.execution_options(
        schema_translate_map={None: schema_name}
    )
    Base.metadata.create_all(isolated_engine)
    try:
        yield isolated_engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        engine.dispose()


def test_concurrent_issues_cannot_overdraw_stock(postgres_engine):
    with Session(postgres_engine) as session, session.begin():
        session.add_all(
            [
                User(
                    id="integration-user",
                    email="integration@example.test",
                    name="Integration User",
                    password_hash="not-used-for-authentication",
                ),
                UnitOfMeasure(code="each", name="Each"),
                Item(
                    id="integration-item",
                    sku="INTEGRATION-1",
                    name="Concurrency test item",
                    uom_code="each",
                ),
                Location(
                    id="integration-location",
                    name="Concurrency test location",
                    location_type="warehouse",
                ),
                StockBalance(
                    item_id="integration-item",
                    location_id="integration-location",
                    quantity=Decimal("5"),
                ),
            ]
        )

    barrier = Barrier(2)

    def issue_four_units() -> str:
        with Session(postgres_engine) as session:
            barrier.wait(timeout=10)
            try:
                with session.begin():
                    post_stock_transaction(
                        session,
                        transaction_type="issue",
                        actor_id="integration-user",
                        movements=[
                            MovementInput(
                                item_id="integration-item",
                                location_id="integration-location",
                                quantity_delta=Decimal("-4"),
                            )
                        ],
                        reason="Concurrent integration test issue",
                        recipient="Integration test",
                    )
            except HTTPException as error:
                if error.status_code != 409:
                    raise
                return "rejected"
            return "posted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: issue_four_units(), range(2)))

    assert sorted(outcomes) == ["posted", "rejected"]
    with Session(postgres_engine) as session:
        balance = session.get(
            StockBalance, ("integration-item", "integration-location")
        )
        assert balance is not None
        assert balance.quantity == Decimal("1.000")
        transactions = list(
            session.scalars(select(StockTransaction.id))
        )
        assert len(transactions) == 1