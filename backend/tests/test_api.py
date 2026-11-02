import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError


def _load_main(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-with-at-least-thirty-two-characters")
    from app import main

    return main


def test_readiness_returns_ok_when_database_is_available(monkeypatch):
    main = _load_main(monkeypatch)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            return None

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(main, "engine", Engine())
    assert main.readiness() == {"status": "ok"}


def test_readiness_returns_503_when_database_is_unavailable(monkeypatch):
    main = _load_main(monkeypatch)

    class Engine:
        def connect(self):
            raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(main, "engine", Engine())
    with pytest.raises(HTTPException) as error:
        main.readiness()

    assert error.value.status_code == 503