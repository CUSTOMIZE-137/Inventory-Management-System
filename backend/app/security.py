from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
from jose import JWTError, jwt
from pwdlib import PasswordHash

from app.config import get_settings


ALGORITHM = "HS256"
password_hash = PasswordHash.recommended()

_login_attempts: dict[str, deque[float]] = {}
_rate_limit_lock = Lock()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def create_access_token(subject: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode(
        {
            "sub": subject,
            "iat": int(now.timestamp()),
            "exp": expires,
            "jti": str(uuid4()),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=ALGORITHM,
    )


def token_subject(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
        )
    except JWTError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def check_login_rate_limit(key: str) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc).timestamp()
    window = settings.login_rate_limit_window_seconds
    limit = settings.login_rate_limit_requests
    with _rate_limit_lock:
        bucket = _login_attempts.setdefault(key, deque())
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Please try again later.",
            )
        bucket.append(now)


def reset_login_rate_limits() -> None:
    with _rate_limit_lock:
        _login_attempts.clear()
