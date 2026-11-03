from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import current_user, require_permission
from app.models import Department, Role, RolePermission, User, UserRole
from app.schemas import (
    LoginRequest,
    RoleRead,
    TokenResponse,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.security import (
    check_login_rate_limit,
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api")


def _build_user_read(session: Session, user: User) -> UserRead:
    roles = sorted(
        session.scalars(
            select(UserRole.role_name).where(UserRole.user_id == user.id)
        )
    )
    permissions = sorted(
        set(
            session.scalars(
                select(RolePermission.permission_name)
                .join(UserRole, UserRole.role_name == RolePermission.role_name)
                .where(UserRole.user_id == user.id)
            )
        )
    )
    return UserRead(
        id=user.id,
        email=user.email,
        name=user.name,
        department_id=user.department_id,
        is_active=user.is_active,
        roles=roles,
        permissions=permissions,
    )


def _validate_roles_and_department(
    session: Session, roles: list[str] | None, department_id: str | None
) -> None:
    if department_id is not None and session.get(Department, department_id) is None:
        raise HTTPException(status_code=404, detail="Department not found")
    if roles:
        existing_roles = set(
            session.scalars(select(Role.name).where(Role.name.in_(set(roles))))
        )
        missing = sorted(set(roles) - existing_roles)
        if missing:
            raise HTTPException(
                status_code=422, detail=f"Unknown role(s): {', '.join(missing)}"
            )


@router.post("/auth/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> TokenResponse:
    client_host = (
        request.client.host if request is not None and request.client else "local"
    )
    check_login_rate_limit(f"{client_host}:{payload.email.lower()}")
    user = session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not user.is_active or not verify_password(
        payload.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/auth/me", response_model=UserRead)
def get_me(
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> UserRead:
    return _build_user_read(session, user)


@router.get("/roles", response_model=list[RoleRead])
def list_roles(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("users:manage")),
) -> list[RoleRead]:
    roles = list(session.scalars(select(Role).order_by(Role.name)))
    result: list[RoleRead] = []
    for role in roles:
        perms = sorted(
            session.scalars(
                select(RolePermission.permission_name).where(
                    RolePermission.role_name == role.name
                )
            )
        )
        result.append(RoleRead(name=role.name, permissions=perms))
    return result


@router.get("/users", response_model=list[UserRead])
def list_users(
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("users:manage")),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[UserRead]:
    users = list(
        session.scalars(
            select(User).order_by(User.email).limit(limit).offset(offset)
        )
    )
    return [_build_user_read(session, u) for u in users]


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("users:manage")),
) -> UserRead:
    email = payload.email.strip().lower()
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="Email is already registered")
    _validate_roles_and_department(session, payload.roles, payload.department_id)
    user = User(
        email=email,
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        department_id=payload.department_id,
        is_active=True,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="User creation conflict") from error
    for role_name in sorted(set(payload.roles)):
        session.add(UserRole(user_id=user.id, role_name=role_name))
    session.flush()
    return _build_user_read(session, user)


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: str,
    payload: UserUpdate,
    session: Session = Depends(get_db),
    _user: User = Depends(require_permission("users:manage")),
) -> UserRead:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    updates = payload.model_dump(exclude_unset=True)
    _validate_roles_and_department(
        session,
        updates.get("roles"),
        updates.get("department_id"),
    )
    if "name" in updates and updates["name"] is not None:
        user.name = updates["name"].strip()
    if "department_id" in updates:
        user.department_id = updates["department_id"]
    if "is_active" in updates and updates["is_active"] is not None:
        user.is_active = updates["is_active"]
    if "password" in updates and updates["password"] is not None:
        user.password_hash = hash_password(updates["password"])
    if "roles" in updates and updates["roles"] is not None:
        session.execute(delete(UserRole).where(UserRole.user_id == user.id))
        for role_name in sorted(set(updates["roles"])):
            session.add(UserRole(user_id=user.id, role_name=role_name))
    session.flush()
    return _build_user_read(session, user)
