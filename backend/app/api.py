from fastapi import APIRouter

from app.routers.auth import router as auth_router
from app.routers.items import router as items_router
from app.routers.transactions import router as transactions_router
from app.routers.users import router as users_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(items_router)
router.include_router(transactions_router)
