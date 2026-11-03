from fastapi import APIRouter

from app.routers.auth import (
    create_user,
    get_me,
    list_roles,
    list_users,
    login,
    router as auth_router,
    update_user,
)
from app.routers.catalog import (
    create_category,
    create_department,
    create_item,
    create_location,
    create_unit,
    create_vendor,
    get_item,
    list_categories,
    list_departments,
    list_items,
    list_locations,
    list_units,
    list_vendors,
    router as catalog_router,
    update_item,
    update_location,
    update_vendor,
)
from app.routers.inventory import (
    create_stock_transaction,
    get_stock,
    get_transaction,
    list_movements,
    list_transactions,
    load_transaction as _load_transaction,
    router as inventory_router,
    transfer_stock,
)
from app.routers.purchase_orders import (
    create_purchase_order,
    get_purchase_order,
    list_purchase_orders,
    receive_purchase_order,
    router as purchase_orders_router,
)
from app.routers.requisitions import (
    approve_requisition,
    confirm_requisition,
    create_requisition,
    dispatch_requisition,
    get_requisition,
    list_requisitions,
    router as requisitions_router,
)

router = APIRouter()
router.include_router(auth_router)
router.include_router(catalog_router)
router.include_router(inventory_router)
router.include_router(purchase_orders_router)
router.include_router(requisitions_router)

__all__ = [
    "router",
    "login",
    "get_me",
    "list_roles",
    "list_users",
    "create_user",
    "update_user",
    "list_items",
    "get_item",
    "create_item",
    "update_item",
    "create_category",
    "list_categories",
    "create_department",
    "list_departments",
    "create_unit",
    "list_units",
    "list_locations",
    "create_location",
    "update_location",
    "create_vendor",
    "list_vendors",
    "update_vendor",
    "list_purchase_orders",
    "get_purchase_order",
    "create_purchase_order",
    "receive_purchase_order",
    "get_stock",
    "list_transactions",
    "get_transaction",
    "list_movements",
    "create_stock_transaction",
    "transfer_stock",
    "list_requisitions",
    "create_requisition",
    "get_requisition",
    "approve_requisition",
    "dispatch_requisition",
    "confirm_requisition",
    "_load_transaction",
]
