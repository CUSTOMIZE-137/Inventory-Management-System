from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Permission, RolePermission, User, UserRole
from app.security import token_subject


bearer_scheme = HTTPBearer()


def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_db),
) -> User:
    user_id = token_subject(credentials.credentials)
    user = session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive account",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_permission(permission_name: str) -> Callable[..., User]:
    def check_permission(
        user: User = Depends(current_user), session: Session = Depends(get_db)
    ) -> User:
        permission = session.scalar(
            select(Permission.name)
            .join(RolePermission, RolePermission.permission_name == Permission.name)
            .join(UserRole, UserRole.role_name == RolePermission.role_name)
            .where(
                UserRole.user_id == user.id,
                Permission.name == permission_name,
            )
        )
        if permission is None:
            raise HTTPException(status_code=403, detail="Permission denied")
        return user

    return check_permission
