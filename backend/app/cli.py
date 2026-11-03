import argparse
from decimal import Decimal
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_engine
from app.models import (
    Base,
    Category,
    Department,
    Item,
    Location,
    Permission,
    PurchaseOrder,
    PurchaseOrderLine,
    Role,
    RolePermission,
    UnitOfMeasure,
    User,
    UserRole,
    Vendor,
)
from app.schemas import MovementInput
from app.security import hash_password
from app.services.inventory import post_stock_transaction


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


def seed_roles_in_session(session: Session) -> None:
    all_permissions = {
        perm for perms in ROLE_PERMISSIONS.values() for perm in perms
    }
    for permission_name in sorted(all_permissions):
        session.merge(Permission(name=permission_name))
    session.flush()
    for role_name, permissions in ROLE_PERMISSIONS.items():
        session.merge(Role(name=role_name))
        session.flush()
        for permission_name in sorted(permissions):
            session.merge(
                RolePermission(
                    role_name=role_name, permission_name=permission_name
                )
            )
    session.flush()


def initialize_roles() -> None:
    with SessionLocal.begin() as session:
        seed_roles_in_session(session)


def create_user_account(
    email: str,
    name: str,
    roles: list[str],
    password: str | None = None,
    department_name: str | None = None,
) -> None:
    secret = password or getpass("Password (minimum 12 characters): ")
    if len(secret) < 12:
        raise ValueError("Password must contain at least 12 characters")
    unknown_roles = set(roles) - set(ROLE_PERMISSIONS)
    if unknown_roles:
        raise ValueError(f"Unknown role(s): {', '.join(sorted(unknown_roles))}")

    with SessionLocal.begin() as session:
        seed_roles_in_session(session)
        if session.scalar(select(User.id).where(User.email == email.lower())):
            raise ValueError("An account with that email already exists")
        department_id: str | None = None
        if department_name:
            dept = session.scalar(
                select(Department).where(Department.name == department_name)
            )
            if dept is None:
                dept = Department(name=department_name)
                session.add(dept)
                session.flush()
            department_id = dept.id

        user = User(
            email=email.lower(),
            name=name,
            password_hash=hash_password(secret),
            department_id=department_id,
            is_active=True,
        )
        session.add(user)
        session.flush()
        for role_name in sorted(set(roles)):
            session.add(UserRole(user_id=user.id, role_name=role_name))


def create_admin(email: str, name: str, password: str | None = None) -> None:
    create_user_account(
        email=email,
        name=name,
        roles=["system_admin"],
        password=password,
    )


def seed_demo() -> None:
    Base.metadata.create_all(get_engine())
    demo_password = "AdminPassword123!"
    with SessionLocal.begin() as session:
        seed_roles_in_session(session)
        dept = session.scalar(
            select(Department).where(Department.name == "Stores & Inventory")
        )
        if dept is None:
            dept = Department(name="Stores & Inventory")
            session.add(dept)
            session.flush()

        accounts = [
            ("admin@example.org", "System Administrator", "system_admin"),
            ("supervisor@example.org", "Stock Supervisor", "supervisor"),
            ("manager@example.org", "Central Store Manager", "store_manager"),
            ("requester@example.org", "Stock Requester", "requester"),
        ]
        admin_user: User | None = None
        for email, name, role_name in accounts:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    name=name,
                    password_hash=hash_password(demo_password),
                    department_id=dept.id,
                    is_active=True,
                )
                session.add(user)
                session.flush()
                session.add(UserRole(user_id=user.id, role_name=role_name))
            if role_name == "system_admin":
                admin_user = user

        for code, uom_name in [("each", "Each"), ("box", "Box"), ("m", "Meter")]:
            session.merge(UnitOfMeasure(code=code, name=uom_name))

        category = session.scalar(
            select(Category).where(Category.name == "Networking & Hardware")
        )
        if category is None:
            category = Category(name="Networking & Hardware")
            session.add(category)
            session.flush()

        wh = session.scalar(
            select(Location).where(Location.name == "Central Warehouse")
        )
        if wh is None:
            wh = Location(name="Central Warehouse", location_type="warehouse")
            session.add(wh)
            session.flush()
            session.add(
                Location(
                    name="Room A - Secure Cage",
                    location_type="room",
                    parent_id=wh.id,
                )
            )

        vendor = session.scalar(
            select(Vendor).where(Vendor.name == "Global Tech Supply")
        )
        if vendor is None:
            vendor = Vendor(
                name="Global Tech Supply",
                email="orders@globaltech.example",
                phone="+1-555-0199",
            )
            session.add(vendor)
            session.flush()

        item_specs = [
            ("NET-SW-24P", "24-Port Managed PoE Switch", "each", Decimal("3"), Decimal("245.0000"), True),
            ("CAB-CAT6-305", "Cat6 UTP Cable Spool (305m)", "box", Decimal("10"), Decimal("85.0000"), False),
            ("SFP-10G-SR", "10GBASE-SR SFP+ Transceiver", "each", Decimal("8"), Decimal("38.5000"), False),
        ]
        created_items: list[Item] = []
        for sku, name, uom, min_stock, cost, req_appr in item_specs:
            existing_item = session.scalar(select(Item).where(Item.sku == sku))
            if existing_item is None:
                existing_item = Item(
                    sku=sku,
                    name=name,
                    category_id=category.id,
                    uom_code=uom,
                    minimum_stock=min_stock,
                    unit_cost=cost,
                    requires_approval=req_appr,
                )
                session.add(existing_item)
                session.flush()
                created_items.append(existing_item)

        if created_items and admin_user is not None:
            post_stock_transaction(
                session,
                transaction_type="receipt",
                actor_id=admin_user.id,
                movements=[
                    MovementInput(
                        item_id=created_items[0].id,
                        location_id=wh.id,
                        quantity_delta=Decimal("6"),
                    ),
                    MovementInput(
                        item_id=created_items[1].id,
                        location_id=wh.id,
                        quantity_delta=Decimal("15"),
                    ),
                    MovementInput(
                        item_id=created_items[2].id,
                        location_id=wh.id,
                        quantity_delta=Decimal("4"),
                    ),
                ],
                reason="Initial opening balance seed",
                reference="OPEN-BAL-001",
                idempotency_key="demo-seed-opening-balance",
            )
            po = PurchaseOrder(
                vendor_id=vendor.id,
                created_by=admin_user.id,
                reference="PO-2026-100",
                status="open",
            )
            session.add(po)
            session.flush()
            session.add(
                PurchaseOrderLine(
                    purchase_order_id=po.id,
                    item_id=created_items[2].id,
                    ordered_qty=Decimal("20"),
                    unit_cost=Decimal("38.5000"),
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory system administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-roles")
    commands.add_parser("seed-demo")

    admin_parser = commands.add_parser("create-admin")
    admin_parser.add_argument("--email", required=True)
    admin_parser.add_argument("--name", required=True)
    admin_parser.add_argument("--password", required=False)

    user_parser = commands.add_parser("create-user")
    user_parser.add_argument("--email", required=True)
    user_parser.add_argument("--name", required=True)
    user_parser.add_argument(
        "--role",
        dest="roles",
        action="append",
        required=True,
        choices=sorted(ROLE_PERMISSIONS),
    )
    user_parser.add_argument("--department", required=False)
    user_parser.add_argument("--password", required=False)

    args = parser.parse_args()
    if args.command == "init-roles":
        initialize_roles()
    elif args.command == "seed-demo":
        seed_demo()
    elif args.command == "create-admin":
        create_admin(args.email, args.name, password=args.password)
    elif args.command == "create-user":
        create_user_account(
            email=args.email,
            name=args.name,
            roles=args.roles,
            password=args.password,
            department_name=args.department,
        )


if __name__ == "__main__":
    main()
