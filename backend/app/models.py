from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Department(TimestampMixin, Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Role(Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)


class Permission(Base):
    __tablename__ = "permissions"

    name: Mapped[str] = mapped_column(String(120), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True
    )
    permission_name: Mapped[str] = mapped_column(
        ForeignKey("permissions.name", ondelete="CASCADE"), primary_key=True
    )


class Category(TimestampMixin, Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )


class UnitOfMeasure(Base):
    __tablename__ = "units_of_measure"

    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)


class Item(TimestampMixin, Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("minimum_stock >= 0", name="ck_items_minimum_stock"),
        CheckConstraint("unit_cost >= 0", name="ck_items_unit_cost"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    uom_code: Mapped[str] = mapped_column(
        ForeignKey("units_of_measure.code"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(
        String(32), default="consumable", server_default="consumable", nullable=False
    )
    tracking_type: Mapped[str] = mapped_column(
        String(24), default="bulk", server_default="bulk", nullable=False
    )
    default_issue_uom: Mapped[str | None] = mapped_column(
        ForeignKey("units_of_measure.code"), nullable=True
    )
    minimum_stock: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )
    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )
    is_restricted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Vendor(TimestampMixin, Base):
    __tablename__ = "vendors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UnitConversion(Base):
    __tablename__ = "unit_conversions"
    __table_args__ = (
        CheckConstraint(
            "multiplier > 0", name="ck_unit_conversions_multiplier_positive"
        ),
        UniqueConstraint("item_id", "from_uom", "to_uom", name="uq_item_uom_conversion"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_uom: Mapped[str] = mapped_column(
        ForeignKey("units_of_measure.code"), nullable=False
    )
    to_uom: Mapped[str] = mapped_column(
        ForeignKey("units_of_measure.code"), nullable=False
    )
    multiplier: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)


class Location(TimestampMixin, Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    location_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120), unique=True)


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        CheckConstraint("ordered_qty > 0"),
        CheckConstraint("received_qty >= 0 AND received_qty <= ordered_qty"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    purchase_order_id: Mapped[str] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), nullable=False)
    ordered_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    received_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)


class Requisition(TimestampMixin, Base):
    __tablename__ = "requisitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    status: Mapped[str] = mapped_column(
        String(32), default="pending_supervisor", nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(120), unique=True)


class RequisitionLine(Base):
    __tablename__ = "requisition_lines"
    __table_args__ = (
        CheckConstraint("quantity_requested > 0"),
        CheckConstraint(
            "quantity_approved >= 0 AND quantity_approved <= quantity_requested"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    requisition_id: Mapped[str] = mapped_column(
        ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), nullable=False)
    quantity_requested: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    quantity_approved: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )


class Donor(TimestampMixin, Base):
    __tablename__ = "donors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    donor_type: Mapped[str] = mapped_column(
        String(40), default="individual", nullable=False
    )
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DonationBatch(TimestampMixin, Base):
    __tablename__ = "donation_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    donor_id: Mapped[str | None] = mapped_column(
        ForeignKey("donors.id", ondelete="SET NULL"), index=True
    )
    received_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    reference: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    received_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list["DonationBatchLine"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class DonationBatchLine(Base):
    __tablename__ = "donation_batch_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_donation_line_qty_pos"),
        CheckConstraint(
            "estimated_unit_cost >= 0", name="ck_donation_line_cost_nonneg"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("donation_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    uom_code: Mapped[str | None] = mapped_column(ForeignKey("units_of_measure.code"))
    estimated_unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )
    batch: Mapped["DonationBatch"] = relationship(back_populates="lines")


class DailyCareLog(TimestampMixin, Base):
    __tablename__ = "daily_care_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    logged_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list["DailyCareLogLine"]] = relationship(
        back_populates="care_log", cascade="all, delete-orphan"
    )


class DailyCareLogLine(Base):
    __tablename__ = "daily_care_log_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_care_log_line_qty_pos"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    care_log_id: Mapped[str] = mapped_column(
        ForeignKey("daily_care_logs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    uom_code: Mapped[str | None] = mapped_column(ForeignKey("units_of_measure.code"))
    normalized_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    care_log: Mapped["DailyCareLog"] = relationship(back_populates="lines")


class StockTakeSession(TimestampMixin, Base):
    __tablename__ = "stock_take_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), nullable=False)
    conducted_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="in_progress", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lines: Mapped[list["StockTakeLine"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class StockTakeLine(Base):
    __tablename__ = "stock_take_lines"
    __table_args__ = (
        CheckConstraint("counted_qty >= 0", name="ck_stock_take_counted_nonneg"),
        UniqueConstraint("session_id", "item_id", name="uq_stock_take_session_item"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("stock_take_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), nullable=False)
    expected_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    counted_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    variance_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(255))
    session: Mapped["StockTakeSession"] = relationship(back_populates="lines")


class ApprovalAction(Base):
    __tablename__ = "approval_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    requisition_id: Mapped[str] = mapped_column(
        ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    acted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StockBalance(Base):
    __tablename__ = "stock_balances"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_stock_balances_nonnegative"),
    )

    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.id"), primary_key=True
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), nullable=False
    )


class StockTransaction(Base):
    __tablename__ = "stock_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_stock_transaction_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    transaction_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    purchase_order_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_orders.id"))
    requisition_id: Mapped[str | None] = mapped_column(ForeignKey("requisitions.id"))
    transaction_subtype: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    donation_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("donation_batches.id"), nullable=True
    )
    daily_care_log_id: Mapped[str | None] = mapped_column(
        ForeignKey("daily_care_logs.id"), nullable=True
    )
    stock_take_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("stock_take_sessions.id"), nullable=True
    )
    reference: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    recipient: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    movements: Mapped[list["StockMovement"]] = relationship(
        back_populates="transaction", order_by="StockMovement.item_id"
    )


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        Index("ix_stock_movements_transaction_id", "transaction_id"),
        Index(
            "ix_stock_movements_item_location_created",
            "item_id",
            "location_id",
            "created_at",
        ),
        CheckConstraint("quantity_after >= 0", name="ck_stock_movement_after_nonnegative"),
        CheckConstraint(
            "quantity_after = quantity_before + quantity_delta",
            name="ck_stock_movement_delta_matches",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("stock_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    quantity_before: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    quantity_after: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    transaction: Mapped["StockTransaction"] = relationship(
        back_populates="movements"
    )
