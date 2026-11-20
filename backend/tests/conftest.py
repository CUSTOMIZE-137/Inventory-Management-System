import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

os.environ["JWT_SECRET"] = "test-secret-key-for-inventory-system-at-least-32-chars"

from app.config import get_settings
from app.db import get_engine, get_sessionmaker, reset_engine_cache
from app.main import app
from app.models import Base, User, UserRole
from app.security import create_access_token, hash_password, reset_login_rate_limits


@pytest.fixture
async def db_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncSession, None]:
    db_file = tmp_path / "test_inventory.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-inventory-system-at-least-32-chars")
    get_settings.cache_clear()
    reset_engine_cache()
    reset_login_rate_limits()

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_sessionmaker()() as session:
        yield session

    await engine.dispose()
    reset_engine_cache()
    get_settings.cache_clear()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest.fixture
async def seeded_users(db_session: AsyncSession) -> dict[str, dict[str, str]]:
    admin = User(
        full_name="Alice Admin",
        email="admin@test.local",
        hashed_password=hash_password("AdminPass123!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    manager = User(
        full_name="Bob Manager",
        email="manager@test.local",
        hashed_password=hash_password("ManagerPass123!"),
        role=UserRole.STORE_MANAGER,
        is_active=True,
    )
    requestor = User(
        full_name="Carol Requestor",
        email="requestor@test.local",
        hashed_password=hash_password("RequestorPass123!"),
        role=UserRole.REQUESTOR,
        is_active=True,
    )
    requestor2 = User(
        full_name="Dave Requestor",
        email="requestor2@test.local",
        hashed_password=hash_password("RequestorPass123!"),
        role=UserRole.REQUESTOR,
        is_active=True,
    )
    db_session.add_all([admin, manager, requestor, requestor2])
    await db_session.commit()

    return {
        "admin": {
            "id": admin.id,
            "token": create_access_token(admin.id, admin.role.value),
        },
        "manager": {
            "id": manager.id,
            "token": create_access_token(manager.id, manager.role.value),
        },
        "requestor": {
            "id": requestor.id,
            "token": create_access_token(requestor.id, requestor.role.value),
        },
        "requestor2": {
            "id": requestor2.id,
            "token": create_access_token(requestor2.id, requestor2.role.value),
        },
    }
