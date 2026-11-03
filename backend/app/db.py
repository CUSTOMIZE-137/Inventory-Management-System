from collections.abc import Generator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


class _LazyEngineProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_engine(), name)


class _LazySessionMakerProxy:
    def __call__(self, *args: Any, **kwargs: Any) -> Session:
        return get_sessionmaker()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(get_sessionmaker(), name)


engine = _LazyEngineProxy()
SessionLocal = _LazySessionMakerProxy()


def get_db() -> Generator[Session, None, None]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
