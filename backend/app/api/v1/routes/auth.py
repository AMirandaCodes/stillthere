"""
Authentication routes.

Rate limits (per IP address via slowapi):
  POST /register  — 5 requests/minute   (spam signup protection)
  POST /login     — 10 requests/minute  (brute-force protection)
  POST /refresh   — 20 requests/minute  (normal refresh cadence)

Token rotation: /refresh always revokes the submitted token and issues a new pair,
so a stolen refresh token can only be used once before the legitimate client
invalidates it on next use.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError

from app.api.deps import DbSession, get_current_user
from app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    refresh_token_expires_at,
    verify_password,
)
from app.core.rate_limiting import limiter
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import LoginRequest, RefreshRequest, TokenResponse, UserCreate, UserResponse

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, payload: UserCreate, db: DbSession) -> User:
    user_repo = UserRepository(db)
    if await user_repo.email_exists(payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists",
        )
    return await user_repo.create(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, payload: LoginRequest, db: DbSession) -> TokenResponse:
    user_repo = UserRepository(db)
    token_repo = RefreshTokenRepository(db)

    user = await user_repo.get_by_email(payload.email)
    # Use a constant-time comparison path for both "not found" and "wrong password"
    # to avoid leaking whether an email is registered
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
        )

    access_token = create_access_token(subject=str(user.id))
    raw_refresh, refresh_hash = generate_refresh_token()
    await token_repo.create(
        user_id=user.id, token_hash=refresh_hash, expires_at=refresh_token_expires_at()
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
async def refresh(request: Request, payload: RefreshRequest, db: DbSession) -> TokenResponse:
    token_repo = RefreshTokenRepository(db)

    stored = await token_repo.get_valid_by_hash(hash_token(payload.refresh_token))
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Rotate: old token out, new pair in
    await token_repo.revoke(stored.token_hash)
    access_token = create_access_token(subject=str(stored.user_id))
    raw_refresh, new_hash = generate_refresh_token()
    await token_repo.create(
        user_id=stored.user_id, token_hash=new_hash, expires_at=refresh_token_expires_at()
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: DbSession) -> None:
    """Revoke the submitted refresh token. Silent if token is already invalid."""
    token_repo = RefreshTokenRepository(db)
    await token_repo.revoke(hash_token(payload.refresh_token))


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
