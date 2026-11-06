"""institutional inventory upgrade

Revision ID: 71d2a469c103
Revises: af436f2f8228
"""

from alembic import op
import sqlalchemy as sa


revision = "71d2a469c103"
down_revision = "af436f2f8228"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "item_type",
                sa.String(length=32),
                server_default="consumable",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "tracking_type",
                sa.String(length=24),
                server_default="bulk",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("default_issue_uom", sa.String(length=24), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_items_default_issue_uom_units",
            "units_of_measure",
            ["default_issue_uom"],
            ["code"],
        )

    op.create_table(
        "unit_conversions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("from_uom", sa.String(length=24), nullable=False),
        sa.Column("to_uom", sa.String(length=24), nullable=False),
        sa.Column("multiplier", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.CheckConstraint(
            "multiplier > 0", name="ck_unit_conversions_multiplier_positive"
        ),
        sa.ForeignKeyConstraint(["from_uom"], ["units_of_measure.code"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_uom"], ["units_of_measure.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "from_uom", "to_uom", name="uq_item_uom_conversion"),
    )
    op.create_index("ix_unit_conversions_item_id", "unit_conversions", ["item_id"])

    op.create_table(
        "donors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("donor_type", sa.String(length=40), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "donation_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("donor_id", sa.String(length=36), nullable=True),
        sa.Column("received_by", sa.String(length=36), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=False),
        sa.Column("received_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["donor_id"], ["donors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["received_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
    )
    op.create_index("ix_donation_batches_donor_id", "donation_batches", ["donor_id"])

    op.create_table(
        "donation_batch_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("location_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("uom_code", sa.String(length=24), nullable=True),
        sa.Column(
            "estimated_unit_cost",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name="ck_donation_line_qty_pos"),
        sa.CheckConstraint(
            "estimated_unit_cost >= 0", name="ck_donation_line_cost_nonneg"
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["donation_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["uom_code"], ["units_of_measure.code"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_donation_batch_lines_batch_id", "donation_batch_lines", ["batch_id"]
    )

    op.create_table(
        "daily_care_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("logged_by", sa.String(length=36), nullable=False),
        sa.Column("department_id", sa.String(length=36), nullable=True),
        sa.Column("location_id", sa.String(length=36), nullable=False),
        sa.Column("activity_type", sa.String(length=64), nullable=False),
        sa.Column("recipient", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["logged_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "daily_care_log_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("care_log_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("uom_code", sa.String(length=24), nullable=True),
        sa.Column("normalized_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_care_log_line_qty_pos"),
        sa.ForeignKeyConstraint(["care_log_id"], ["daily_care_logs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["uom_code"], ["units_of_measure.code"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_daily_care_log_lines_care_log_id",
        "daily_care_log_lines",
        ["care_log_id"],
    )

    op.create_table(
        "stock_take_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("location_id", sa.String(length=36), nullable=False),
        sa.Column("conducted_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conducted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "stock_take_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("expected_qty", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("counted_qty", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("variance_qty", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("notes", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "counted_qty >= 0", name="ck_stock_take_counted_nonneg"
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["stock_take_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "item_id", name="uq_stock_take_session_item"),
    )
    op.create_index("ix_stock_take_lines_session_id", "stock_take_lines", ["session_id"])

    with op.batch_alter_table("stock_transactions") as batch_op:
        batch_op.add_column(sa.Column("transaction_subtype", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("donation_batch_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("daily_care_log_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("stock_take_session_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_stock_transactions_donation_batch_id_donation_batches",
            "donation_batches",
            ["donation_batch_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_stock_transactions_daily_care_log_id_daily_care_logs",
            "daily_care_logs",
            ["daily_care_log_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_stock_transactions_stock_take_session_id_stock_take_sessions",
            "stock_take_sessions",
            ["stock_take_session_id"],
            ["id"],
        )
    op.create_index(
        "ix_stock_transactions_transaction_subtype",
        "stock_transactions",
        ["transaction_subtype"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_stock_transactions_transaction_subtype",
        table_name="stock_transactions",
    )
    with op.batch_alter_table("stock_transactions") as batch_op:
        batch_op.drop_column("stock_take_session_id")
        batch_op.drop_column("daily_care_log_id")
        batch_op.drop_column("donation_batch_id")
        batch_op.drop_column("transaction_subtype")

    op.drop_index("ix_stock_take_lines_session_id", table_name="stock_take_lines")
    op.drop_table("stock_take_lines")
    op.drop_table("stock_take_sessions")
    op.drop_index(
        "ix_daily_care_log_lines_care_log_id", table_name="daily_care_log_lines"
    )
    op.drop_table("daily_care_log_lines")
    op.drop_table("daily_care_logs")
    op.drop_index(
        "ix_donation_batch_lines_batch_id", table_name="donation_batch_lines"
    )
    op.drop_table("donation_batch_lines")
    op.drop_index("ix_donation_batches_donor_id", table_name="donation_batches")
    op.drop_table("donation_batches")
    op.drop_table("donors")
    op.drop_index("ix_unit_conversions_item_id", table_name="unit_conversions")
    op.drop_table("unit_conversions")
    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_constraint("fk_items_default_issue_uom_units", type_="foreignkey")
        batch_op.drop_column("default_issue_uom")
        batch_op.drop_column("tracking_type")
        batch_op.drop_column("item_type")
