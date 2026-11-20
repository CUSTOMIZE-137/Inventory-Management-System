from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from app.models import TransactionType, UserRole


def _serialize_stock_number(value: Decimal) -> int | float:
    """Serialize whole-number Decimals as JSON ints and fractional values as floats."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


# Preserves Decimal precision internally while emitting clean JSON numbers to the PWA client.
StockNumber = Annotated[
    Decimal,
    PlainSerializer(_serialize_stock_number, return_type=float, when_used="json"),
]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    id: str
    full_name: str
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class UserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.REQUESTOR


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class ItemCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    barcode: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=240)
    category: str = Field(default="General", min_length=1, max_length=120)
    unit: str = Field(default="pcs", min_length=1, max_length=32)
    reorder_level: int = Field(default=5, ge=0, le=1_000_000)


class ItemUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    barcode: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=240)
    category: str | None = Field(default=None, min_length=1, max_length=120)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    reorder_level: int | None = Field(default=None, ge=0, le=1_000_000)


class ItemRead(BaseModel):
    id: str
    sku: str
    barcode: str | None
    name: str
    category: str
    unit: str
    current_stock: StockNumber
    reorder_level: int
    is_low_stock: bool
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_model(cls, item: object) -> "ItemRead":
        current_stock = Decimal(str(getattr(item, "current_stock")))
        reorder_level = int(getattr(item, "reorder_level"))
        return cls(
            id=getattr(item, "id"),
            sku=getattr(item, "sku"),
            barcode=getattr(item, "barcode"),
            name=getattr(item, "name"),
            category=getattr(item, "category"),
            unit=getattr(item, "unit"),
            current_stock=current_stock,
            reorder_level=reorder_level,
            is_low_stock=current_stock <= Decimal(reorder_level),
            updated_at=getattr(item, "updated_at"),
        )


class TransactionCreate(BaseModel):
    """Payload for recording a single ledger event (or one entry inside an offline sync batch)."""

    # Client-generated UUIDv4 preventing double-application when offline queues retry.
    idempotency_key: UUID
    item_id: str = Field(min_length=1, max_length=36)
    transaction_type: TransactionType
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=3)
    counted_stock: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=3)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_quantity_for_type(self) -> "TransactionCreate":
        """Enforce mutually appropriate quantity fields per transaction type:
        - STOCK_IN / STOCK_OUT require a positive delta (`quantity`) and forbid `counted_stock`.
        - ADJUSTMENT requires an absolute verified physical count (`counted_stock`).
        """
        if self.transaction_type in {TransactionType.STOCK_IN, TransactionType.STOCK_OUT}:
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("quantity must be a positive number for STOCK_IN and STOCK_OUT")
            if self.counted_stock is not None:
                raise ValueError("counted_stock is only valid for ADJUSTMENT transactions")
        elif self.transaction_type == TransactionType.ADJUSTMENT:
            if self.counted_stock is None:
                raise ValueError("counted_stock is required for ADJUSTMENT transactions")
        return self


class TransactionRead(BaseModel):
    id: str
    idempotency_key: UUID
    item_id: str
    item_sku: str
    item_name: str
    item_unit: str
    user_id: str
    user_full_name: str
    user_email: str
    user_role: UserRole
    transaction_type: TransactionType
    quantity: StockNumber
    previous_stock: StockNumber
    remaining_stock: StockNumber
    notes: str | None
    created_at: datetime


class TransactionSyncRequest(BaseModel):
    transactions: list[TransactionCreate] = Field(min_length=1, max_length=200)


class TransactionSyncItemResult(BaseModel):
    idempotency_key: UUID
    status: Literal["created", "replayed", "rejected"]
    transaction: TransactionRead | None = None
    error: str | None = None


class TransactionSyncResponse(BaseModel):
    results: list[TransactionSyncItemResult]
    created_count: int
    replayed_count: int
    rejected_count: int


class AuditSummaryRead(BaseModel):
    total_users: int
    active_users: int
    users_by_role: dict[str, int]
    total_items: int
    low_stock_items: int
    total_stock_units: StockNumber
    total_transactions: int
    recent_transactions: list[TransactionRead]
