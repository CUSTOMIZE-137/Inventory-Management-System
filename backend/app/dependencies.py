from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import User, UserRole
from app.security import decode_token_subject

bearer_scheme = HTTPBearer()


async def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate Bearer JWT and reload the live User record from the database.

    Re-fetching from the DB ensures role updates and account deactivations take effect
    immediately rather than waiting for an issued JWT to expire.
    """
    user_id = decode_token_subject(credentials.credentials)
    user = await session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive account",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(*allowed_roles: UserRole) -> Callable[..., User]:
    """Dependency factory enforcing that the authenticated user holds one of `allowed_roles`."""
    allowed = frozenset(allowed_roles)

    async def check_role(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role permissions for this action",
            )
        return user

    return check_role
