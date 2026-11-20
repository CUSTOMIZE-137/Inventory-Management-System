import os
from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from app.config import get_settings


def _configure_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Enable SQLite foreign-key enforcement and busy retry timeout on every new connection."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        # SQLite disables foreign-key checks by default; enable so ON DELETE RESTRICT holds.
        cursor.execute("PRAGMA foreign_keys=ON")
        # Wait up to 5s on lock contention instead of failing immediately with "database is locked".
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


@lru_cache
def get_engine() -> AsyncEngine:
    """Create and cache the async SQLAlchemy engine tuned for Postgres or SQLite."""
    settings = get_settings()
    url = settings.async_database_url
    is_serverless = bool(os.environ.get("VERCEL"))
    options: dict[str, Any] = {}
    if url.startswith("postgresql"):
        connect_args: dict[str, Any] = {}
        # Disable asyncpg's prepared statement cache when using PgBouncer/Neon transaction
        # poolers or serverless environments to avoid DuplicatePreparedStatementError.
        if "-pooler." in url or is_serverless:
            connect_args["statement_cache_size"] = 0
        if connect_args:
            options["connect_args"] = connect_args
        if is_serverless:
            # Serverless functions should not hold idle pooled sockets across frozen invocations
            # or separate event loops; NullPool opens and closes cleanly per checkout.
            options["poolclass"] = NullPool
        else:
            # pool_pre_ping discards stale/idle connections on long-lived servers.
            options.update(pool_pre_ping=True, pool_size=5, max_overflow=10, pool_timeout=30)
    elif ":memory:" in url:
        # StaticPool keeps a single shared connection so in-memory SQLite tables persist across sessions.
        options["pool_pre_ping"] = True
        options["poolclass"] = StaticPool
        options["connect_args"] = {"check_same_thread": False}
    elif url.startswith("sqlite"):
        options["pool_pre_ping"] = True
        options["connect_args"] = {"timeout": 30}

    engine = create_async_engine(url, **options)
    if url.startswith("sqlite"):
        _configure_sqlite_pragmas(engine)
    return engine


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return a cached async sessionmaker with expire_on_commit disabled for post-commit reads."""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )


def reset_engine_cache() -> None:
    """Clear cached engine/sessionmaker instances (used by test fixtures when switching DB URLs)."""
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()


# Lazy proxies defer engine creation until first use so importing app modules never triggers
# premature DB connections before environment variables or test overrides are configured.
class _LazyEngineProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_engine(), name)


class _LazySessionMakerProxy:
    def __call__(self, *args: Any, **kwargs: Any) -> AsyncSession:
        return get_sessionmaker()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(get_sessionmaker(), name)


engine = _LazyEngineProxy()
SessionLocal = _LazySessionMakerProxy()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency providing one transactional AsyncSession per request.

    Commits automatically when the route handler returns cleanly; rolls back on any exception.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
