from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
from jose import JWTError, jwt
from pwdlib import PasswordHash

from app.config import get_settings

ALGORITHM = "HS256"
# Uses pwdlib's recommended modern password hasher (Argon2id).
password_hash = PasswordHash.recommended()

# In-memory sliding-window tracker for login throttling, keyed by "<client_ip>:<email>".
_login_attempts: dict[str, deque[float]] = {}
_rate_limit_lock = Lock()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def create_access_token(subject: str, role: str) -> str:
    """Issue a signed HS256 JWT carrying user ID (`sub`), role claim, and unique `jti`."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode(
        {
            "sub": subject,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": expires,
            "jti": str(uuid4()),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=ALGORITHM,
    )


def decode_token_subject(token: str) -> str | None:
    """Verify JWT signature and expiry, returning the `sub` (user ID) or None if invalid."""
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
    """Enforce a thread-safe sliding-window rate limit on login attempts.

    Opportunistically prunes idle keys to keep memory bounded on long-running workers.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc).timestamp()
    window = settings.login_rate_limit_window_seconds
    limit = settings.login_rate_limit_requests
    with _rate_limit_lock:
        # Evict completely stale buckets before evaluating the current caller.
        expired_keys = [
            k for k, bucket in _login_attempts.items() if not bucket or now - bucket[-1] > window
        ]
        for k in expired_keys:
            del _login_attempts[k]
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
