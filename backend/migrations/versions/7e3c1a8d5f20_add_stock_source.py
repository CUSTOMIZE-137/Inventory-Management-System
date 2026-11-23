"""add purchased and donated stock source

Revision ID: 7e3c1a8d5f20
Revises: 6b4f9c2e8a10
"""

from alembic import op
import sqlalchemy as sa


revision = "7e3c1a8d5f20"
down_revision = "6b4f9c2e8a10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        stock_source_enum = sa.Enum("purchased", "donated", name="stock_source")
        stock_source_enum.create(bind, checkfirst=True)
        op.execute(
            "DROP TRIGGER IF EXISTS reject_stock_audit_mutation ON stock_transactions"
        )

    with op.batch_alter_table("stock_transactions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "stock_source",
                sa.Enum("purchased", "donated", name="stock_source", native_enum=is_pg),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_stock_transactions_stock_source",
            ["stock_source"],
        )

    bind.execute(
        sa.text(
            "UPDATE stock_transactions "
            "SET stock_source = 'purchased' "
            "WHERE transaction_type = 'STOCK_IN'"
        )
    )

    if is_pg:
        op.execute(
            """
            CREATE TRIGGER reject_stock_audit_mutation
            BEFORE UPDATE OR DELETE OR TRUNCATE ON stock_transactions
            FOR EACH STATEMENT
            EXECUTE FUNCTION reject_stock_audit_mutation()
            """
        )


def downgrade() -> None:
    with op.batch_alter_table("stock_transactions") as batch_op:
        batch_op.drop_index("ix_stock_transactions_stock_source")
        batch_op.drop_column("stock_source")

    if op.get_bind().dialect.name == "postgresql":
        sa.Enum(name="stock_source").drop(op.get_bind(), checkfirst=True)
