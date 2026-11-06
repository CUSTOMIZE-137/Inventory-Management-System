"""consolidate user roles

Revision ID: 72f0c4d2a981
Revises: 71d2a469c103
"""

from alembic import op


revision = "72f0c4d2a981"
down_revision = "71d2a469c103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO roles (name)
        VALUES ('system_administrator'), ('manager')
        ON CONFLICT (name) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE user_roles
        SET role_name = 'system_administrator'
        WHERE role_name = 'system_admin'
        """
    )
    op.execute(
        """
        UPDATE user_roles
        SET role_name = 'manager'
        WHERE role_name IN ('warehouse_clerk', 'supervisor', 'store_manager', 'procurement')
        """
    )
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_name IN (
            'system_admin', 'warehouse_clerk', 'supervisor',
            'store_manager', 'procurement'
        )
        """
    )
    op.execute(
        """
        DELETE FROM roles
        WHERE name IN (
            'system_admin', 'warehouse_clerk', 'supervisor',
            'store_manager', 'procurement'
        )
        """
    )


def downgrade() -> None:
    op.execute("INSERT INTO roles (name) VALUES ('system_admin') ON CONFLICT (name) DO NOTHING")
