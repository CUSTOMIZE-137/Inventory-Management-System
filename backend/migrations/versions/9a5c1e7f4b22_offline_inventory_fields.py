"""add offline catalog fields

Revision ID: 9a5c1e7f4b22
Revises: 72f0c4d2a981
"""

from alembic import op
import sqlalchemy as sa


revision = "9a5c1e7f4b22"
down_revision = "72f0c4d2a981"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(sa.Column("barcode", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column(
                "reorder_threshold",
                sa.Numeric(precision=18, scale=3),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.create_unique_constraint("uq_items_barcode", ["barcode"])


def downgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_constraint("uq_items_barcode", type_="unique")
        batch_op.drop_column("is_deleted")
        batch_op.drop_column("reorder_threshold")
        batch_op.drop_column("barcode")
