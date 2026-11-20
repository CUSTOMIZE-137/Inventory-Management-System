from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import current_user, require_roles
from app.models import Item, User, UserRole, utc_now
from app.schemas import ItemCreate, ItemRead, ItemUpdate

router = APIRouter(prefix="/api/v1/items", tags=["items"])


@router.get("", response_model=list[ItemRead])
async def list_items(
    q: str | None = Query(default=None, max_length=120),
    category: str | None = Query(default=None, max_length=120),
    low_stock_only: bool = Query(default=False),
    updated_since: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(current_user),
) -> list[ItemRead]:
    """List catalog items with optional search, category, low-stock, and incremental sync filters."""
    stmt = select(Item)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Item.name).like(needle),
                func.lower(Item.sku).like(needle),
                func.lower(func.coalesce(Item.barcode, "")).like(needle),
                func.lower(Item.category).like(needle),
            )
        )
    if category and category.strip():
        stmt = stmt.where(func.lower(Item.category) == category.strip().lower())
    if low_stock_only:
        stmt = stmt.where(Item.current_stock <= Item.reorder_level)
    if updated_since is not None:
        # Supports incremental offline cache refreshes by fetching only recently changed items.
        stmt = stmt.where(Item.updated_at > updated_since)

    stmt = stmt.order_by(Item.name, Item.sku).limit(limit).offset(offset)
    items = list(await session.scalars(stmt))
    return [ItemRead.from_model(item) for item in items]


@router.get("/lookup/{code}", response_model=ItemRead)
async def lookup_item_by_code(
    code: str,
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(current_user),
) -> ItemRead:
    """Resolve a scanned barcode or typed SKU (case-insensitive) to a catalog item."""
    cleaned = code.strip()
    stmt = select(Item).where(
        or_(
            func.lower(Item.barcode) == cleaned.lower(),
            func.lower(Item.sku) == cleaned.lower(),
        )
    )
    item = await session.scalar(stmt)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No item found matching code '{cleaned}'",
        )
    return ItemRead.from_model(item)


@router.get("/{item_id}", response_model=ItemRead)
async def get_item(
    item_id: str,
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(current_user),
) -> ItemRead:
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return ItemRead.from_model(item)


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(
    payload: ItemCreate,
    session: AsyncSession = Depends(get_db),
    _manager: User = Depends(require_roles(UserRole.STORE_MANAGER)),
) -> ItemRead:
    """Register a new catalog SKU (STORE_MANAGER only).

    New items always start at current_stock = 0; opening stock must be recorded as a
    STOCK_IN ledger transaction so every unit on hand is auditable.
    """
    sku = payload.sku.strip().upper()
    barcode = payload.barcode.strip() if payload.barcode and payload.barcode.strip() else None

    if await session.scalar(select(Item.id).where(func.lower(Item.sku) == sku.lower())) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"SKU '{sku}' already exists")
    if barcode is not None:
        existing_barcode = await session.scalar(select(Item.id).where(Item.barcode == barcode))
        if existing_barcode is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Barcode '{barcode}' is already assigned to another item",
            )

    item = Item(
        sku=sku,
        barcode=barcode,
        name=payload.name.strip(),
        category=payload.category.strip(),
        unit=payload.unit.strip(),
        reorder_level=payload.reorder_level,
    )
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Item SKU or barcode already exists",
        ) from exc
    return ItemRead.from_model(item)


@router.patch("/{item_id}", response_model=ItemRead)
async def update_item(
    item_id: str,
    payload: ItemUpdate,
    session: AsyncSession = Depends(get_db),
    _manager: User = Depends(require_roles(UserRole.STORE_MANAGER)),
) -> ItemRead:
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    updates = payload.model_dump(exclude_unset=True)
    if "sku" in updates and updates["sku"] is not None:
        new_sku = updates["sku"].strip().upper()
        conflict = await session.scalar(
            select(Item.id).where(func.lower(Item.sku) == new_sku.lower(), Item.id != item.id)
        )
        if conflict is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"SKU '{new_sku}' already exists")
        item.sku = new_sku
    if "barcode" in updates:
        raw_bc = updates["barcode"]
        new_bc = raw_bc.strip() if raw_bc and raw_bc.strip() else None
        if new_bc is not None:
            conflict = await session.scalar(
                select(Item.id).where(Item.barcode == new_bc, Item.id != item.id)
            )
            if conflict is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Barcode '{new_bc}' is already assigned to another item",
                )
        item.barcode = new_bc
    if "name" in updates and updates["name"] is not None:
        item.name = updates["name"].strip()
    if "category" in updates and updates["category"] is not None:
        item.category = updates["category"].strip()
    if "unit" in updates and updates["unit"] is not None:
        item.unit = updates["unit"].strip()
    if "reorder_level" in updates and updates["reorder_level"] is not None:
        item.reorder_level = updates["reorder_level"]

    item.updated_at = utc_now()
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Item SKU or barcode conflicts with an existing item",
        ) from exc
    return ItemRead.from_model(item)
