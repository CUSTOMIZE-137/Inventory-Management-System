from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRole(str, Enum):
    """System-wide RBAC roles with strict separation of duties:
    - ADMIN: user lifecycle governance and read-only global audit oversight.
    - STORE_MANAGER: catalog administration, STOCK_IN intake, and ADJUSTMENT reconciliations.
    - REQUESTOR: catalog lookup and STOCK_OUT take-outs.
    """

    ADMIN = "ADMIN"
    STORE_MANAGER = "STORE_MANAGER"
    REQUESTOR = "REQUESTOR"


class TransactionType(str, Enum):
    """Ledger event types supported by the append-only stock_transactions table."""

    STOCK_IN = "STOCK_IN"
    STOCK_OUT = "STOCK_OUT"
    ADJUSTMENT = "ADJUSTMENT"


class StockSource(str, Enum):
    """How inventory units entered the single stock location."""

    PURCHASED = "purchased"
    DONATED = "donated"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=True),
        nullable=False,
        default=UserRole.REQUESTOR,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # lazy="raise" prevents accidental implicit I/O under AsyncSession; callers must eager-load.
    transactions: Mapped[list["StockTransaction"]] = relationship(
        back_populates="user", lazy="raise"
    )


class Item(Base):
    """Catalog SKU record holding the authoritative current_stock balance.

    Stock quantities are never mutated directly via item endpoints; they are updated
    atomically alongside a StockTransaction ledger row in services.inventory.
    """

    __tablename__ = "items"
    __table_args__ = (
        # Database-level last-resort guard ensuring stock can never drop below zero.
        CheckConstraint("current_stock >= 0", name="ck_items_current_stock_nonnegative"),
        CheckConstraint("reorder_level >= 0", name="ck_items_reorder_level_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    barcode: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    category: Mapped[str] = mapped_column(
        String(120), default="General", server_default="General", nullable=False, index=True
    )
    unit: Mapped[str] = mapped_column(
        String(32), default="pcs", server_default="pcs", nullable=False
    )
    # Numeric(18, 3) supports exact fractional units (e.g., kg, liters) without float rounding drift.
    current_stock: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), server_default="0", nullable=False
    )
    reorder_level: Mapped[int] = mapped_column(
        Integer, default=5, server_default="5", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    transactions: Mapped[list["StockTransaction"]] = relationship(
        back_populates="item", lazy="raise"
    )


class StockTransaction(Base):
    """Immutable, append-only audit ledger of every inventory balance change.

    Each row captures an idempotency_key (for safe offline retry/replay) along with
    before/after balance snapshots (previous_stock -> remaining_stock) for full traceability.
    """

    __tablename__ = "stock_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_stock_transactions_idempotency_key"),
        CheckConstraint("quantity > 0", name="ck_stock_transactions_quantity_positive"),
        CheckConstraint(
            "previous_stock >= 0", name="ck_stock_transactions_previous_stock_nonnegative"
        ),
        CheckConstraint(
            "remaining_stock >= 0", name="ck_stock_transactions_remaining_stock_nonnegative"
        ),
        # Composite indexes accelerate per-item and per-user chronological ledger queries.
        Index("ix_stock_transactions_item_created", "item_id", "created_at"),
        Index("ix_stock_transactions_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    # RESTRICT prevents deleting items or users that have historical ledger entries.
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    transaction_type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, name="transaction_type", native_enum=True),
        nullable=False,
        index=True,
    )
    stock_source: Mapped[StockSource | None] = mapped_column(
        SAEnum(StockSource, name="stock_source", native_enum=True),
        nullable=True,
        index=True,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    previous_stock: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    remaining_stock: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    item: Mapped["Item"] = relationship(back_populates="transactions", lazy="raise")
    user: Mapped["User"] = relationship(back_populates="transactions", lazy="raise")
