from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=128)
    department_id: str | None = None
    roles: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    department_id: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)
    roles: list[str] | None = None


class UserRead(BaseModel):
    id: str
    email: str
    name: str
    department_id: str | None
    is_active: bool
    roles: list[str]
    permissions: list[str]

    model_config = ConfigDict(from_attributes=True)


class RoleRead(BaseModel):
    name: str
    permissions: list[str]


class ItemCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=240)
    category_id: str | None = None
    uom_code: str
    minimum_stock: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=3)
    unit_cost: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=4)
    is_restricted: bool = False
    requires_approval: bool = False


class ItemUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=240)
    category_id: str | None = None
    uom_code: str | None = None
    minimum_stock: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=3)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    is_restricted: bool | None = None
    requires_approval: bool | None = None
    is_active: bool | None = None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None


class CategoryRead(BaseModel):
    id: str
    name: str
    parent_id: str | None

    model_config = ConfigDict(from_attributes=True)


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class DepartmentRead(BaseModel):
    id: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class UnitOfMeasureCreate(BaseModel):
    code: str = Field(min_length=1, max_length=24)
    name: str = Field(min_length=1, max_length=80)


class UnitOfMeasureRead(BaseModel):
    code: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class ItemRead(ItemCreate):
    id: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    location_type: Literal["warehouse", "room", "shelf", "bin"]
    parent_id: str | None = None


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    location_type: Literal["warehouse", "room", "shelf", "bin"] | None = None
    parent_id: str | None = None
    is_active: bool | None = None


class LocationRead(LocationCreate):
    id: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class MovementInput(BaseModel):
    item_id: str
    location_id: str
    quantity_delta: Decimal = Field(max_digits=18, decimal_places=3)


class StockPostRequest(BaseModel):
    type: Literal["receipt", "issue", "adjustment"]
    movements: list[MovementInput] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)
    reference: str | None = Field(default=None, max_length=120)
    recipient: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def movement_signs_match_type(self) -> "StockPostRequest":
        if self.type == "receipt" and any(m.quantity_delta <= 0 for m in self.movements):
            raise ValueError("Receipt quantities must be positive")
        if self.type == "issue" and any(m.quantity_delta >= 0 for m in self.movements):
            raise ValueError("Issue quantities must be negative")
        if self.type == "adjustment" and any(m.quantity_delta == 0 for m in self.movements):
            raise ValueError("Adjustment quantities must be non-zero")
        if self.type == "issue" and not self.recipient:
            raise ValueError("recipient is required for issues")
        return self


class TransferRequest(BaseModel):
    item_id: str
    source_location_id: str
    destination_location_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    reason: str = Field(min_length=1, max_length=2000)
    reference: str | None = Field(default=None, max_length=120)


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)


class VendorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    is_active: bool | None = None


class VendorRead(BaseModel):
    id: str
    name: str
    email: str | None
    phone: str | None
    is_active: bool = True

    model_config = ConfigDict(from_attributes=True)


class PurchaseOrderLineInput(BaseModel):
    item_id: str
    ordered_qty: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=4)


class PurchaseOrderLineRead(BaseModel):
    id: str
    item_id: str
    ordered_qty: Decimal
    received_qty: Decimal
    unit_cost: Decimal

    model_config = ConfigDict(from_attributes=True)


class PurchaseOrderCreate(BaseModel):
    vendor_id: str
    reference: str = Field(min_length=1, max_length=120)
    lines: list[PurchaseOrderLineInput] = Field(min_length=1, max_length=100)


class PurchaseOrderRead(BaseModel):
    id: str
    vendor_id: str
    created_by: str
    reference: str | None
    status: str
    created_at: datetime
    lines: list[PurchaseOrderLineRead]

    model_config = ConfigDict(from_attributes=True)


class PurchaseOrderReceiveLine(BaseModel):
    purchase_order_line_id: str
    location_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)


class PurchaseOrderReceive(BaseModel):
    lines: list[PurchaseOrderReceiveLine] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)


class RequisitionLineInput(BaseModel):
    item_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)


class RequisitionCreate(BaseModel):
    department_id: str | None = None
    reference: str | None = Field(default=None, max_length=120)
    lines: list[RequisitionLineInput] = Field(min_length=1, max_length=100)


class RequisitionLineApprovalInput(BaseModel):
    item_id: str
    quantity_approved: Decimal = Field(ge=0, max_digits=18, decimal_places=3)


class ApprovalInput(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)
    line_approvals: list[RequisitionLineApprovalInput] | None = None


class ApprovalActionRead(BaseModel):
    id: str
    stage: str
    actor_id: str
    decision: str
    comment: str | None
    acted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RequisitionLineRead(BaseModel):
    id: str
    item_id: str
    quantity_requested: Decimal
    quantity_approved: Decimal

    model_config = ConfigDict(from_attributes=True)


class RequisitionRead(BaseModel):
    id: str
    reference: str | None
    status: str
    requester_id: str
    department_id: str | None
    created_at: datetime
    lines: list[RequisitionLineRead]
    approvals: list[ApprovalActionRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class DispatchInput(BaseModel):
    location_id: str


class StockMovementRead(BaseModel):
    id: str | None = None
    transaction_id: str | None = None
    item_id: str
    location_id: str
    quantity_delta: Decimal
    quantity_before: Decimal
    quantity_after: Decimal
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TransactionRead(BaseModel):
    id: str
    transaction_type: str
    actor_id: str | None = None
    purchase_order_id: str | None = None
    requisition_id: str | None = None
    reference: str | None
    reason: str
    recipient: str | None
    created_at: datetime | None = None
    movements: list[StockMovementRead]

    model_config = ConfigDict(from_attributes=True)


class StockLevelRead(BaseModel):
    item_id: str
    location_id: str
    quantity: Decimal

    model_config = ConfigDict(from_attributes=True)
