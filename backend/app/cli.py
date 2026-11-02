import argparse
from getpass import getpass

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Permission, Role, RolePermission, User, UserRole
from app.security import hash_password


ROLE_PERMISSIONS = {
    "warehouse_clerk": {"catalog:read", "inventory:read", "inventory:transact"},
    "supervisor": {
        "catalog:read",
        "inventory:read",
        "requisitions:approve",
        "reports:read",
    },
    "store_manager": {
        "catalog:read",
        "inventory:read",
        "inventory:transact",
        "requisitions:approve",
        "requisitions:dispatch",
        "reports:read",
    },
    "requester": {
        "catalog:read",
        "inventory:read",
        "requisitions:request",
        "requisitions:confirm",
    },
    "procurement": {"catalog:read", "catalog:write", "reports:read"},
    "system_admin": {
        "catalog:read",
        "catalog:write",
        "inventory:read",
        "inventory:transact",
        "reports:read",
        "users:manage",
        "requisitions:request",
        "requisitions:approve",
        "requisitions:dispatch",
        "requisitions:confirm",
    },
}


def initialize_roles() -> None:
    with SessionLocal.begin() as session:
        for role_name, permissions in ROLE_PERMISSIONS.items():
            session.merge(Role(name=role_name))
            for permission_name in permissions:
                session.merge(Permission(name=permission_name))
                session.merge(
                    RolePermission(role_name=role_name, permission_name=permission_name)
                )


def create_admin(email: str, name: str) -> None:
    password = getpass("Password (minimum 12 characters): ")
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    with SessionLocal.begin() as session:
        if session.scalar(select(User.id).where(User.email == email.lower())):
            raise ValueError("An account with that email already exists")
        user = User(
            email=email.lower(),
            name=name,
            password_hash=hash_password(password),
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.add(UserRole(user_id=user.id, role_name="system_admin"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory system administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-roles")
    admin_parser = commands.add_parser("create-admin")
    admin_parser.add_argument("--email", required=True)
    admin_parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if args.command == "init-roles":
        initialize_roles()
    else:
        create_admin(args.email, args.name)


if __name__ == "__main__":
    main()
