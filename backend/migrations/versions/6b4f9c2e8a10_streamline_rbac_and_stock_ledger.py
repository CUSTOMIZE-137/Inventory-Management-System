"""streamline rbac and stock ledger

Revision ID: 6b4f9c2e8a10
Revises: 2c8d4e1a7f30
Create Date: 2026-11-20 09:30:00.000000
"""

from decimal import Decimal
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "6b4f9c2e8a10"
down_revision = "2c8d4e1a7f30"
branch_labels = None
depends_on = None

LEGACY_TABLES_IN_DROP_ORDER = (
    "stock_movements",
    "stock_transactions",
    "stock_balances",
    "stock_take_lines",
    "stock_take_sessions",
    "daily_care_log_lines",
    "daily_care_logs",
    "donation_batch_lines",
    "donation_batches",
    "donors",
    "approval_actions",
    "requisition_lines",
    "requisitions",
    "purchase_order_lines",
    "purchase_orders",
    "vendors",
    "unit_conversions",
    "locations",
    "items",
    "units_of_measure",
    "categories",
    "user_roles",
    "role_permissions",
    "permissions",
    "roles",
    "users",
    "departments",
)


def _map_role(legacy_roles: set[str]) -> str:
    if legacy_roles & {"system_administrator", "system_admin", "ADMIN"}:
        return "ADMIN"
    if legacy_roles & {
        "manager",
        "store_manager",
        "supervisor",
        "warehouse_clerk",
        "procurement",
        "STORE_MANAGER",
    }:
        return "STORE_MANAGER"
    return "REQUESTOR"


def _map_tx_type(tx_type: str, delta: Decimal) -> str:
    if tx_type == "receipt":
        return "STOCK_IN"
    if tx_type == "issue":
        return "STOCK_OUT"
    if tx_type == "adjustment":
        return "ADJUSTMENT"
    return "STOCK_IN" if delta >= 0 else "STOCK_OUT"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if dialect == "postgresql":
        for table_name in ("stock_transactions", "stock_movements"):
            if table_name in existing_tables:
                op.execute(
                    f"DROP TRIGGER IF EXISTS reject_stock_audit_mutation ON {table_name}"
                )

    # 1. Snapshot legacy data before replacing schema with streamlined 3-table design.
    migrated_users: list[dict[str, object]] = []
    if "users" in existing_tables:
        role_map: dict[str, set[str]] = {}
        if "user_roles" in existing_tables:
            for user_id, role_name in bind.execute(
                text("SELECT user_id, role_name FROM user_roles")
            ).fetchall():
                role_map.setdefault(user_id, set()).add(role_name)
        for row in bind.execute(
            text("SELECT id, name, email, password_hash, is_active, created_at FROM users")
        ).fetchall():
            migrated_users.append(
                {
                    "id": row[0],
                    "full_name": row[1],
                    "email": row[2],
                    "hashed_password": row[3],
                    "role": _map_role(role_map.get(row[0], set())),
                    "is_active": bool(row[4]),
                    "created_at": row[5],
                }
            )

    migrated_items: list[dict[str, object]] = []
    if "items" in existing_tables:
        cat_map: dict[str, str] = {}
        if "categories" in existing_tables:
            for cid, cname in bind.execute(
                text("SELECT id, name FROM categories")
            ).fetchall():
                cat_map[cid] = cname
        bal_map: dict[str, Decimal] = {}
        if "stock_balances" in existing_tables:
            for item_id, qty in bind.execute(
                text("SELECT item_id, quantity FROM stock_balances")
            ).fetchall():
                bal_map[item_id] = bal_map.get(item_id, Decimal("0")) + Decimal(str(qty))
        for row in bind.execute(
            text(
                "SELECT id, sku, barcode, name, category_id, uom_code, "
                "reorder_threshold, minimum_stock, updated_at FROM items"
            )
        ).fetchall():
            reorder_raw = row[6] if row[6] is not None and Decimal(str(row[6])) > 0 else row[7]
            reorder_level = max(0, int(Decimal(str(reorder_raw or 5))))
            current_stock = max(Decimal("0"), bal_map.get(row[0], Decimal("0")))
            migrated_items.append(
                {
                    "id": row[0],
                    "sku": row[1],
                    "barcode": row[2],
                    "name": row[3],
                    "category": cat_map.get(row[4], "General") if row[4] else "General",
                    "unit": row[5] or "pcs",
                    "current_stock": current_stock,
                    "reorder_level": reorder_level,
                    "updated_at": row[8],
                }
            )

    migrated_transactions: list[dict[str, object]] = []
    if "stock_transactions" in existing_tables and "stock_movements" in existing_tables:
        seen_keys: set[str] = set()
        for row in bind.execute(
            text(
                "SELECT sm.id, st.idempotency_key, sm.item_id, st.actor_id, "
                "st.transaction_type, sm.quantity_delta, sm.quantity_before, "
                "sm.quantity_after, st.reason, sm.created_at "
                "FROM stock_movements sm "
                "JOIN stock_transactions st ON st.id = sm.transaction_id "
                "ORDER BY sm.created_at ASC"
            )
        ).fetchall():
            delta = Decimal(str(row[5]))
            qty = abs(delta)
            if qty <= 0:
                continue
            key = row[1] if row[1] and row[1] not in seen_keys and len(row[1]) <= 36 else str(uuid4())
            seen_keys.add(key)
            migrated_transactions.append(
                {
                    "id": row[0],
                    "idempotency_key": key,
                    "item_id": row[2],
                    "user_id": row[3],
                    "transaction_type": _map_tx_type(row[4], delta),
                    "quantity": qty,
                    "previous_stock": max(Decimal("0"), Decimal(str(row[6]))),
                    "remaining_stock": max(Decimal("0"), Decimal(str(row[7]))),
                    "notes": row[8],
                    "created_at": row[9],
                }
            )

    # 2. Drop obsolete tables cleanly in dependency order.
    for table_name in LEGACY_TABLES_IN_DROP_ORDER:
        if table_name in existing_tables:
            op.drop_table(table_name)

    # 3. Create streamlined, rock-solid 3-table schema.
    user_role_enum = sa.Enum("ADMIN", "STORE_MANAGER", "REQUESTOR", name="user_role")
    tx_type_enum = sa.Enum("STOCK_IN", "STOCK_OUT", "ADJUSTMENT", name="transaction_type")

    users_table = op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=512), nullable=False),
        sa.Column("role", user_role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    items_table = op.create_table(
        "items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sku", sa.String(length=80), nullable=False),
        sa.Column("barcode", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("category", sa.String(length=120), server_default="General", nullable=False),
        sa.Column("unit", sa.String(length=32), server_default="pcs", nullable=False),
        sa.Column(
            "current_stock",
            sa.Numeric(precision=18, scale=3),
            server_default="0",
            nullable=False,
        ),
        sa.Column("reorder_level", sa.Integer(), server_default="5", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("current_stock >= 0", name="ck_items_current_stock_nonnegative"),
        sa.CheckConstraint("reorder_level >= 0", name="ck_items_reorder_level_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_items_sku"), "items", ["sku"], unique=True)
    op.create_index(op.f("ix_items_barcode"), "items", ["barcode"], unique=True)
    op.create_index(op.f("ix_items_category"), "items", ["category"], unique=False)

    tx_table = op.create_table(
        "stock_transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("transaction_type", tx_type_enum, nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("previous_stock", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("remaining_stock", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_stock_transactions_quantity_positive"),
        sa.CheckConstraint(
            "previous_stock >= 0", name="ck_stock_transactions_previous_stock_nonnegative"
        ),
        sa.CheckConstraint(
            "remaining_stock >= 0", name="ck_stock_transactions_remaining_stock_nonnegative"
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_stock_transactions_idempotency_key"),
    )
    op.create_index(op.f("ix_stock_transactions_item_id"), "stock_transactions", ["item_id"])
    op.create_index(op.f("ix_stock_transactions_user_id"), "stock_transactions", ["user_id"])
    op.create_index(
        op.f("ix_stock_transactions_transaction_type"),
        "stock_transactions",
        ["transaction_type"],
    )
    op.create_index(op.f("ix_stock_transactions_created_at"), "stock_transactions", ["created_at"])
    op.create_index(
        "ix_stock_transactions_item_created",
        "stock_transactions",
        ["item_id", "created_at"],
    )
    op.create_index(
        "ix_stock_transactions_user_created",
        "stock_transactions",
        ["user_id", "created_at"],
    )

    # 4. Re-insert preserved data into streamlined tables.
    if migrated_users:
        op.bulk_insert(users_table, migrated_users)
    if migrated_items:
        op.bulk_insert(items_table, migrated_items)
    if migrated_transactions:
        op.bulk_insert(tx_table, migrated_transactions)

    # 5. Enforce ledger immutability on PostgreSQL.
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_stock_audit_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'stock_transactions ledger records are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER reject_stock_audit_mutation
            BEFORE UPDATE OR DELETE OR TRUNCATE ON stock_transactions
            FOR EACH STATEMENT
            EXECUTE FUNCTION reject_stock_audit_mutation()
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS reject_stock_audit_mutation ON stock_transactions")
    op.drop_table("stock_transactions")
    op.drop_table("items")
    op.drop_table("users")
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS transaction_type")
        op.execute("DROP TYPE IF EXISTS user_role")
