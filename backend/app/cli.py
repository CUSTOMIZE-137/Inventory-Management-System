import argparse
import asyncio
from decimal import Decimal
from getpass import getpass
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy import select

from app.db import SessionLocal, get_engine
from app.models import Base, Item, TransactionType, User, UserRole
from app.schemas import TransactionCreate
from app.security import hash_password
from app.services.inventory import record_stock_transaction


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


async def seed_demo() -> None:
    """Idempotently seed default demo accounts (one per RBAC role) and sample store items."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    demo_password = "AdminPassword123!"
    accounts = [
        ("admin@example.org", "System Administrator", UserRole.ADMIN),
        ("manager@example.org", "Store Manager", UserRole.STORE_MANAGER),
        ("requestor@example.org", "Store Requestor", UserRole.REQUESTOR),
    ]

    async with SessionLocal() as session:
        async with session.begin():
            manager_user: User | None = None
            for email, full_name, role in accounts:
                user = await session.scalar(select(User).where(User.email == email))
                if user is None:
                    user = User(
                        full_name=full_name,
                        email=email,
                        hashed_password=hash_password(demo_password),
                        role=role,
                        is_active=True,
                    )
                    session.add(user)
                    await session.flush()
                if role == UserRole.STORE_MANAGER:
                    manager_user = user

            sample_items = [
                ("MED-GLV-M", "6001000000012", "Nitrile Examination Gloves (Medium)", "Medical Supplies", "boxes", 10, Decimal("28")),
                ("HYG-SAN-500", "6001000000029", "Hand Sanitizer Gel 500ml", "Hygiene", "pcs", 8, Decimal("6")),
                ("OFF-PPR-A4", "6001000000036", "A4 Copy Paper 80gsm", "Stationery", "boxes", 5, Decimal("14")),
                ("KIT-RIC-25", "6001000000043", "Long Grain Rice", "Kitchen", "kg", 20, Decimal("15")),
                ("CLN-DET-5L", "6001000000050", "Liquid Disinfectant 5L", "Cleaning", "liters", 10, Decimal("25")),
            ]

            for sku, barcode, name, category, unit, reorder_level, initial_qty in sample_items:
                item = await session.scalar(select(Item).where(Item.sku == sku))
                if item is None:
                    item = Item(
                        sku=sku,
                        barcode=barcode,
                        name=name,
                        category=category,
                        unit=unit,
                        reorder_level=reorder_level,
                    )
                    session.add(item)
                    await session.flush()
                    if manager_user is not None and initial_qty > 0:
                        # Seed opening stock through the ledger with a deterministic uuid5 key
                        # so re-running seed-demo can never double-credit inventory.
                        await record_stock_transaction(
                            session,
                            user=manager_user,
                            payload=TransactionCreate(
                                idempotency_key=uuid5(NAMESPACE_URL, f"seed:{sku}"),
                                item_id=item.id,
                                transaction_type=TransactionType.STOCK_IN,
                                quantity=initial_qty,
                                notes="Initial store opening stock",
                            ),
                        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory Management System CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="Seed default ADMIN, STORE_MANAGER, REQUESTOR accounts and sample items")

    user_parser = commands.add_parser("create-user", help="Create a new user account")
    user_parser.add_argument("--email", required=True)
    user_parser.add_argument("--name", required=True)
    user_parser.add_argument("--role", required=True, choices=[r.value for r in UserRole])
    user_parser.add_argument("--password", required=False)

    args = parser.parse_args()
    if args.command == "seed-demo":
        asyncio.run(seed_demo())
        print("Demo accounts and sample inventory seeded.")
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
