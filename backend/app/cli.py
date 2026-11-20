import argparse
import asyncio
from getpass import getpass

from sqlalchemy import select

from app.db import SessionLocal, get_engine
from app.models import Base, User, UserRole
from app.security import hash_password


async def create_user_account(
    *,
    email: str,
    full_name: str,
    role: UserRole,
    password: str | None = None,
) -> None:
    secret = password or getpass("Password (minimum 8 characters): ")
    if len(secret) < 8:
        raise ValueError("Password must contain at least 8 characters")

    async with SessionLocal() as session:
        async with session.begin():
            existing = await session.scalar(select(User.id).where(User.email == email.lower()))
            if existing is not None:
                raise ValueError("An account with that email already exists")
            session.add(
                User(
                    full_name=full_name.strip(),
                    email=email.strip().lower(),
                    hashed_password=hash_password(secret),
                    role=role,
                    is_active=True,
                )
            )


async def create_superuser(*, email: str, full_name: str, password: str | None = None) -> None:
    """Create the first or an additional administrator account."""
    secret = password or getpass("Password (minimum 8 characters): ")
    if len(secret) < 8:
        raise ValueError("Password must contain at least 8 characters")

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        async with session.begin():
            existing = await session.scalar(select(User.id).where(User.email == email.strip().lower()))
            if existing is not None:
                raise ValueError("An account with that email already exists")
            session.add(
                User(
                    full_name=full_name.strip(),
                    email=email.strip().lower(),
                    hashed_password=hash_password(secret),
                    role=UserRole.ADMIN,
                    is_active=True,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory Management System CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    superuser_parser = commands.add_parser(
        "create-superuser", help="Create an administrator account for initial system access"
    )
    superuser_parser.add_argument("--email", required=True)
    superuser_parser.add_argument("--name", required=True)
    superuser_parser.add_argument("--password", required=False)

    user_parser = commands.add_parser("create-user", help="Create a new user account")
    user_parser.add_argument("--email", required=True)
    user_parser.add_argument("--name", required=True)
    user_parser.add_argument("--role", required=True, choices=[r.value for r in UserRole])
    user_parser.add_argument("--password", required=False)

    args = parser.parse_args()
    if args.command == "create-superuser":
        asyncio.run(
            create_superuser(
                email=args.email,
                full_name=args.name,
                password=args.password,
            )
        )
        print(f"Created ADMIN user: {args.email}")
    elif args.command == "create-user":
        asyncio.run(
            create_user_account(
                email=args.email,
                full_name=args.name,
                role=UserRole(args.role),
                password=args.password,
            )
        )
        print(f"Created {args.role} user: {args.email}")


if __name__ == "__main__":
    main()
