from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import require_permission
from app.models import (
    Category,
    Department,
    Item,
    Location,
    UnitOfMeasure,
    User,
    Vendor,
)
from app.schemas import (
    CategoryCreate,
    CategoryRead,
    DepartmentCreate,
    DepartmentRead,
    ItemCreate,
    ItemRead,
    ItemUpdate,
    LocationCreate,
    LocationRead,
    LocationUpdate,
    UnitOfMeasureCreate,
    UnitOfMeasureRead,
    VendorCreate,
    VendorRead,
    VendorUpdate,
)

router = APIRouter(prefix="/api")

LOCATION_HIERARCHY_RANK = {
    "warehouse": 0,
    "room": 1,
    "shelf": 2,
    "bin": 3,
}


def _check_category_parent(
    session: Session, parent_id: str | None, category_id: str | None = None
) -> None:
    if parent_id is None:
        return
    if category_id is not None and parent_id == category_id:
        raise HTTPException(
            status_code=422, detail="A category cannot be its own parent"
        )
    visited = {category_id} if category_id else set()
    current_id: str | None = parent_id
    while current_id is not None:
        if current_id in visited:
            raise HTTPException(
                status_code=422, detail="Category parent cycle detected"
            )
        visited.add(current_id)
        parent = session.get(Category, current_id)
        if parent is None:
            raise HTTPException(
                status_code=404, detail="Parent category not found"
            )
        current_id = parent.parent_id


def _check_location_parent(
    session: Session,
    location_type: str,
    parent_id: str | None,
    location_id: str | None = None,
) -> None:
    if parent_id is None:
        return
    if location_id is not None and parent_id == location_id:
        raise HTTPException(
            status_code=422, detail="A location cannot be its own parent"
        )
    parent = session.get(Location, parent_id)
    if parent is None or not parent.is_active:
        raise HTTPException(
            status_code=404, detail="Active parent location not found"
        )
    if (
        LOCATION_HIERARCHY_RANK[parent.location_type]
        >= LOCATION_HIERARCHY_RANK[location_type]
    ):
        raise HTTPException(
            status_code=422,
            detail=f"A '{location_type}' cannot be nested inside a '{parent.location_type}'",
        )
    visited = {location_id} if location_id else set()
    current_id: str | None = parent_id
    while current_id is not None:
        if current_id in visited:
            raise HTTPException(
                status_code=422, detail="Location parent cycle detected"
            )
        visited.add(current_id)
        node = session.get(Location, current_id)
        if node is None:
            break
        current_id = node.parent_id


def _validate_item_references(
    session: Session, uom_code: str | None, category_id: str | None
) -> None:
    if uom_code is not None and session.get(UnitOfMeasure, uom_code) is None:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    if category_id is not None and session.get(Category, category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")


@router.get("/items", response_model=list[ItemRead])
def list_items(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Item]:
    query = select(Item)
    if not include_inactive:
        query = query.where(Item.is_active.is_(True))
    return list(
        session.scalars(query.order_by(Item.name).limit(limit).offset(offset))
    )


@router.get("/items/{item_id}", response_model=ItemRead)
def get_item(
    item_id: str,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post(
    "/items",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
)
def create_item(
    payload: ItemCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> Item:
    if session.scalar(select(Item.id).where(Item.sku == payload.sku)) is not None:
        raise HTTPException(status_code=409, detail="Item SKU already exists")
    _validate_item_references(session, payload.uom_code, payload.category_id)
    item = Item(**payload.model_dump())
    session.add(item)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Item conflicts with existing catalog record"
        ) from error
    return item


@router.patch("/items/{item_id}", response_model=ItemRead)
def update_item(
    item_id: str,
    payload: ItemUpdate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    updates = payload.model_dump(exclude_unset=True)
    if "sku" in updates and updates["sku"] != item.sku:
        if (
            session.scalar(select(Item.id).where(Item.sku == updates["sku"]))
            is not None
        ):
            raise HTTPException(status_code=409, detail="Item SKU already exists")
    _validate_item_references(
        session, updates.get("uom_code"), updates.get("category_id")
    )
    for field, value in updates.items():
        setattr(item, field, value)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Item update conflicts with existing record"
        ) from error
    return item


@router.post("/categories", response_model=CategoryRead, status_code=201)
def create_category(
    payload: CategoryCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> dict[str, str | None]:
    if (
        session.scalar(select(Category.id).where(Category.name == payload.name))
        is not None
    ):
        raise HTTPException(status_code=409, detail="Category name already exists")
    _check_category_parent(session, payload.parent_id)
    category = Category(**payload.model_dump())
    session.add(category)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Category creation conflict"
        ) from error
    return {
        "id": category.id,
        "name": category.name,
        "parent_id": category.parent_id,
    }


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, str | None]]:
    return [
        {"id": category.id, "name": category.name, "parent_id": category.parent_id}
        for category in session.scalars(
            select(Category).order_by(Category.name).limit(limit).offset(offset)
        )
    ]


@router.post("/departments", response_model=DepartmentRead, status_code=201)
def create_department(
    payload: DepartmentCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> dict[str, str]:
    if (
        session.scalar(
            select(Department.id).where(Department.name == payload.name)
        )
        is not None
    ):
        raise HTTPException(status_code=409, detail="Department already exists")
    department = Department(name=payload.name)
    session.add(department)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Department creation conflict"
        ) from error
    return {"id": department.id, "name": department.name}


@router.get("/departments", response_model=list[DepartmentRead])
def list_departments(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, str]]:
    return [
        {"id": department.id, "name": department.name}
        for department in session.scalars(
            select(Department).order_by(Department.name).limit(limit).offset(offset)
        )
    ]


@router.post("/units", response_model=UnitOfMeasureRead, status_code=201)
def create_unit(
    payload: UnitOfMeasureCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> dict[str, str]:
    if session.get(UnitOfMeasure, payload.code) is not None:
        raise HTTPException(
            status_code=409, detail="Unit of measure code already exists"
        )
    unit = UnitOfMeasure(**payload.model_dump())
    session.add(unit)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Unit of measure conflict"
        ) from error
    return {"code": unit.code, "name": unit.name}


@router.get("/units", response_model=list[UnitOfMeasureRead])
def list_units(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, str]]:
    return [
        {"code": unit.code, "name": unit.name}
        for unit in session.scalars(
            select(UnitOfMeasure)
            .order_by(UnitOfMeasure.name)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/locations", response_model=list[LocationRead])
def list_locations(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("inventory:read")),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Location]:
    query = select(Location)
    if not include_inactive:
        query = query.where(Location.is_active.is_(True))
    return list(
        session.scalars(query.order_by(Location.name).limit(limit).offset(offset))
    )


@router.post("/locations", response_model=LocationRead, status_code=201)
def create_location(
    payload: LocationCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> Location:
    _check_location_parent(session, payload.location_type, payload.parent_id)
    location = Location(**payload.model_dump())
    session.add(location)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Location creation conflict"
        ) from error
    return location


@router.patch("/locations/{location_id}", response_model=LocationRead)
def update_location(
    location_id: str,
    payload: LocationUpdate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> Location:
    location = session.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")
    updates = payload.model_dump(exclude_unset=True)
    next_type = updates.get("location_type", location.location_type)
    next_parent = (
        updates["parent_id"] if "parent_id" in updates else location.parent_id
    )
    _check_location_parent(
        session, next_type, next_parent, location_id=location.id
    )
    for field, value in updates.items():
        setattr(location, field, value)
    session.flush()
    return location


@router.post("/vendors", response_model=VendorRead, status_code=201)
def create_vendor(
    payload: VendorCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> dict[str, object]:
    if (
        session.scalar(select(Vendor.id).where(Vendor.name == payload.name))
        is not None
    ):
        raise HTTPException(status_code=409, detail="Vendor name already exists")
    vendor = Vendor(**payload.model_dump())
    session.add(vendor)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Vendor creation conflict"
        ) from error
    return {
        "id": vendor.id,
        "name": vendor.name,
        "email": vendor.email,
        "phone": vendor.phone,
        "is_active": vendor.is_active,
    }


@router.get("/vendors", response_model=list[VendorRead])
def list_vendors(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:read")),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    query = select(Vendor)
    if not include_inactive:
        query = query.where(Vendor.is_active.is_(True))
    return [
        {
            "id": vendor.id,
            "name": vendor.name,
            "email": vendor.email,
            "phone": vendor.phone,
            "is_active": vendor.is_active,
        }
        for vendor in session.scalars(
            query.order_by(Vendor.name).limit(limit).offset(offset)
        )
    ]


@router.patch("/vendors/{vendor_id}", response_model=VendorRead)
def update_vendor(
    vendor_id: str,
    payload: VendorUpdate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("catalog:write")),
) -> Vendor:
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] != vendor.name:
        if (
            session.scalar(
                select(Vendor.id).where(Vendor.name == updates["name"])
            )
            is not None
        ):
            raise HTTPException(
                status_code=409, detail="Vendor name already exists"
            )
    for field, value in updates.items():
        setattr(vendor, field, value)
    session.flush()
    return vendor
