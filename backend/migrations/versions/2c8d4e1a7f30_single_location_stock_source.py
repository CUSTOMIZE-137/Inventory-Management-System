"""align with a single inventory location and stock source

Revision ID: 2c8d4e1a7f30
Revises: 9a5c1e7f4b22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from decimal import Decimal


revision = "2c8d4e1a7f30"
down_revision = "9a5c1e7f4b22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        for table_name in ("stock_transactions", "stock_movements"):
            op.execute(
                f"DROP TRIGGER IF EXISTS reject_stock_audit_mutation ON {table_name}"
            )

    location = bind.execute(
        text(
            "SELECT id FROM locations "
            "WHERE is_active = :active "
            "ORDER BY created_at, id LIMIT 1"
        ),
        {"active": True},
    ).first()
    if location is None:
        location_id = "main-inventory"
        bind.execute(
            text(
                "INSERT INTO locations "
                "(id, name, location_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (:id, 'Main Inventory', 'warehouse', NULL, :active, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": location_id, "active": True},
        )
    else:
        location_id = location[0]
        bind.execute(
            text(
                "UPDATE locations SET name = 'Main Inventory', "
                "location_type = 'warehouse', parent_id = NULL "
                "WHERE id = :id"
            ),
            {"id": location_id},
        )

    # Merge stock balances before collapsing every historical location.
    balances = bind.execute(
        text("SELECT item_id, quantity FROM stock_balances")
    ).fetchall()
    totals = {}
    for item_id, quantity in balances:
        totals[item_id] = totals.get(item_id, Decimal("0")) + Decimal(str(quantity))
    bind.execute(text("DELETE FROM stock_balances"))
    for item_id, quantity in totals.items():
        bind.execute(
            text(
                "INSERT INTO stock_balances (item_id, location_id, quantity) "
                "VALUES (:item_id, :location_id, :quantity)"
            ),
            {
                "item_id": item_id,
                "location_id": location_id,
                "quantity": str(quantity),
            },
        )

    for table in (
        "donation_batch_lines",
        "daily_care_logs",
        "stock_take_sessions",
        "stock_movements",
    ):
        bind.execute(
            text(f"UPDATE {table} SET location_id = :location_id"),
            {"location_id": location_id},
        )

    bind.execute(
        text("UPDATE locations SET is_active = :active WHERE id != :location_id"),
        {"active": False, "location_id": location_id},
    )

    with op.batch_alter_table("stock_transactions") as batch_op:
        batch_op.add_column(
            sa.Column("stock_source", sa.String(length=24), nullable=True)
        )
        batch_op.create_index(
            "ix_stock_transactions_stock_source",
            ["stock_source"],
        )

    bind.execute(
        text(
            "UPDATE stock_transactions SET stock_source = 'donated' "
            "WHERE donation_batch_id IS NOT NULL "
            "OR transaction_subtype = 'donation_receipt'"
        )
    )
    bind.execute(
        text(
            "UPDATE stock_transactions SET stock_source = 'purchased' "
            "WHERE purchase_order_id IS NOT NULL "
            "OR transaction_subtype = 'purchase_receipt'"
        )
    )

    if is_pg:
        for table_name in ("stock_transactions", "stock_movements"):
            op.execute(
                f"""
                CREATE TRIGGER reject_stock_audit_mutation
                BEFORE UPDATE OR DELETE OR TRUNCATE ON {table_name}
                FOR EACH STATEMENT
                EXECUTE FUNCTION reject_stock_audit_mutation()
                """
            )


def downgrade() -> None:
    with op.batch_alter_table("stock_transactions") as batch_op:
        batch_op.drop_index("ix_stock_transactions_stock_source")
        batch_op.drop_column("stock_source")
