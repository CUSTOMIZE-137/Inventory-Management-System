import argparse
import asyncio
from getpass import getpass
from sqlalchemy import select

from app.db import get_engine, get_sessionmaker
from app.models import User, UserRole
from app.security import hash_password


def _prompt_non_empty(label: str) -> str:
    value = input(f"{label}: ").strip()
    if not value:
        raise ValueError(f"{label} cannot be empty")
    return value


def _prompt_password() -> str:
    secret = getpass("Password (minimum 8 characters): ")
    if len(secret) < 8:
        raise ValueError("Password must contain at least 8 characters")
    confirm = getpass("Confirm password: ")
    if secret != confirm:
        raise ValueError("Passwords do not match")
    return secret


async def create_user_account(
    *,
    email: str,
    full_name: str,
    role: UserRole,
    password: str | None = None,
) -> User:
    normalized_email = email.strip().lower()
    normalized_name = full_name.strip()
    if not normalized_name:
        raise ValueError("Full name cannot be empty")
    if "@" not in normalized_email or " " in normalized_email:
        raise ValueError("Please provide a valid email address")

    secret = password if password is not None else _prompt_password()
    if len(secret) < 8:
        raise ValueError("Password must contain at least 8 characters")

    session_factory = get_sessionmaker()
    try:
        async with session_factory() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(User.id).where(User.email == normalized_email)
                )
                if existing is not None:
                    raise ValueError(f"An account with email '{normalized_email}' already exists")
                user = User(
                    full_name=normalized_name,
                    email=normalized_email,
                    hashed_password=hash_password(secret),
                    role=role,
                    is_active=True,
                )
                session.add(user)
                await session.flush()
                return user
    finally:
        await get_engine().dispose()


async def create_superuser(
    *,
    email: str,
    full_name: str,
    password: str | None = None,
) -> User:
    """Create an active ADMIN superuser account."""
    return await create_user_account(
        email=email,
        full_name=full_name,
        role=UserRole.ADMIN,
        password=password,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory Management System CLI")
    commands = parser.add_subparsers(dest="command", required=True)

    superuser_parser = commands.add_parser(
        "create-superuser",
        help="Create an administrator (ADMIN) superuser account (interactive if flags are omitted)",
    )
    superuser_parser.add_argument("--email", required=False, help="Administrator email address")
    superuser_parser.add_argument("--name", required=False, help="Administrator full name")
    superuser_parser.add_argument(
        "--password",
        required=False,
        help="Password (omit to enter securely via interactive prompt)",
    )

    user_parser = commands.add_parser("create-user", help="Create a new user account")
    user_parser.add_argument("--email", required=False, help="User email address")
    user_parser.add_argument("--name", required=False, help="User full name")
    user_parser.add_argument("--role", required=True, choices=[r.value for r in UserRole])
    user_parser.add_argument(
        "--password",
        required=False,
        help="Password (omit to enter securely via interactive prompt)",
    )

    args = parser.parse_args()
    if args.command == "create-superuser":
        full_name = args.name or _prompt_non_empty("Full name")
        email = args.email or _prompt_non_empty("Email address")
        user = asyncio.run(
            create_superuser(
                email=email,
                full_name=full_name,
                password=args.password,
            )
        )
        print(f"Created ADMIN superuser: {user.full_name} <{user.email}>")
    elif args.command == "create-user":
        full_name = args.name or _prompt_non_empty("Full name")
        email = args.email or _prompt_non_empty("Email address")
        user = asyncio.run(
            create_user_account(
                email=email,
                full_name=full_name,
                role=UserRole(args.role),
                password=args.password,
            )
        )
        print(f"Created {user.role.value} user: {user.full_name} <{user.email}>")


if __name__ == "__main__":
    main()
