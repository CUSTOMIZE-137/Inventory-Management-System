from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _normalize_asyncpg_query(url: str) -> str:
    """Translate libpq query params (e.g. Neon's sslmode/channel_binding) for asyncpg."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    params: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "sslmode":
            params.append(("ssl", value))
        elif key == "channel_binding":
            continue
        else:
            params.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(params)))


def to_async_database_url(url: str) -> str:
    """Resolve an async SQLAlchemy driver URL (asyncpg for PostgreSQL, aiosqlite for SQLite).

    asyncpg runs natively on Windows ProactorEventLoop as well as Linux/Docker,
    avoiding psycopg's SelectorEventLoop restriction under Uvicorn on Windows.
    """
    if url.startswith("sqlite+aiosqlite://"):
        return url
    if url.startswith("sqlite+pysqlite://"):
        return url.replace("sqlite+pysqlite://", "sqlite+aiosqlite://", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return _normalize_asyncpg_query(url)
    if url.startswith("postgresql+psycopg://"):
        return _normalize_asyncpg_query(
            url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        )
    if url.startswith("postgres://"):
        return _normalize_asyncpg_query(url.replace("postgres://", "postgresql+asyncpg://", 1))
    if url.startswith("postgresql://"):
        return _normalize_asyncpg_query(url.replace("postgresql://", "postgresql+asyncpg://", 1))
    return url


def to_sync_database_url(url: str) -> str:
    """Resolve a synchronous SQLAlchemy driver URL for tools like Alembic."""
    if url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite+aiosqlite://", "sqlite+pysqlite://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and backend/.env."""

    database_url: str
    # Optional direct (non-PgBouncer) URL used for DDL migrations when database_url is pooled.
    database_url_unpooled: str | None = None
    # Enforce a 32+ character signing secret for HS256 JWT tokens.
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = Field(default=60, gt=0)
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "http://localhost:3000",
        ]
    )
    login_rate_limit_requests: int = Field(default=15, gt=0)
    login_rate_limit_window_seconds: int = Field(default=60, gt=0)

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def async_database_url(self) -> str:
        return to_async_database_url(self.database_url)

    @property
    def alembic_database_url(self) -> str:
        # Prefer an explicit unpooled URL; otherwise strip Neon's "-pooler." host suffix
        # because transactional DDL migrations should bypass transaction-mode poolers.
        raw = self.database_url_unpooled or self.database_url.replace("-pooler.", ".")
        return to_sync_database_url(raw)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings singleton (cleared by tests when env vars change)."""
    return Settings()
