"""remove legacy demo users and demo seed records

Revision ID: 9c2d5e1f8b30
Revises: 7e3c1a8d5f20
"""

from alembic import op
import sqlalchemy as sa


revision = "9c2d5e1f8b30"
down_revision = "7e3c1a8d5f20"
branch_labels = None
depends_on = None

LEGACY_DEMO_EMAILS = (
    "admin@example.org",
    "manager@example.org",
    "requestor@example.org",
)

LEGACY_DEMO_SKUS = (
    "MED-GLV-M",
    "HYG-SAN-500",
    "OFF-PPR-A4",
    "KIT-RIC-25",
    "CLN-DET-5L",
)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.execute(
            "DROP TRIGGER IF EXISTS reject_stock_audit_mutation ON stock_transactions"
        )

    # 1. Remove stock transactions tied to the legacy demo accounts first (ON DELETE RESTRICT).
    bind.execute(
        sa.text(
            "DELETE FROM stock_transactions "
            "WHERE user_id IN ("
            "  SELECT id FROM users WHERE LOWER(email) IN :demo_emails"
            ")"
        ).bindparams(sa.bindparam("demo_emails", expanding=True)),
        {"demo_emails": list(LEGACY_DEMO_EMAILS)},
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

    # 2. Remove legacy seeded demo catalog items only if they have no remaining transactions.
    bind.execute(
        sa.text(
            "DELETE FROM items "
            "WHERE sku IN :demo_skus "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM stock_transactions WHERE stock_transactions.item_id = items.id"
            "  )"
        ).bindparams(sa.bindparam("demo_skus", expanding=True)),
        {"demo_skus": list(LEGACY_DEMO_SKUS)},
    )

    # 3. Remove the legacy demo user accounts.
    bind.execute(
        sa.text("DELETE FROM users WHERE LOWER(email) IN :demo_emails").bindparams(
            sa.bindparam("demo_emails", expanding=True)
        ),
        {"demo_emails": list(LEGACY_DEMO_EMAILS)},
    )


def downgrade() -> None:
    # Irreversible data cleanup: removed legacy demo accounts are intentionally not recreated.
    pass
