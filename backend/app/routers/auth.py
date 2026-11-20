from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import current_user
from app.models import User
from app.schemas import LoginRequest, TokenResponse, UserRead
from app.security import check_login_rate_limit, create_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    email = payload.email.strip().lower()
    # Scope rate-limiting per (client IP, normalized email) to throttle credential stuffing
    # without locking out unrelated users behind a shared NAT.
    client_host = request.client.host if request.client else "local"
    check_login_rate_limit(f"{client_host}:{email}")

    user = await session.scalar(select(User).where(User.email == email))
    # Return the same generic 401 message for missing, inactive, or wrong-password accounts
    # to avoid leaking account existence.
    if user is None or not user.is_active or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(subject=user.id, role=user.role.value)
    return TokenResponse(access_token=token, user=UserRead.model_validate(user))


@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(current_user)) -> UserRead:
    return UserRead.model_validate(user)
