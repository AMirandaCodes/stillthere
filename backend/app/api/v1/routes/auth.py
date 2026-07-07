"""
Authentication routes.

Rate limits (per IP address via slowapi) — hardcoded in the @limiter.limit()
decorators below. The RATE_LIMIT_REQUESTS / RATE_LIMIT_PERIOD config values
do NOT control these; each endpoint has a deliberately different limit:
  POST /register  — 5 requests/minute   (spam signup protection)
  POST /login     — 10 requests/minute  (brute-force protection)
  POST /refresh   — 20 requests/minute  (normal refresh cadence)

Token rotation: /refresh always revokes the submitted token and issues a new pair,
so a stolen refresh token can only be used once before the legitimate client
invalidates it on next use.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, get_current_user
from app.core.rate_limiting import limiter
from app.models.user import User
from app.schemas.user import LoginRequest, RefreshRequest, TokenResponse, UserCreate, UserResponse
from app.services.auth_service import AuthError, AuthService

router = APIRouter()

_REGISTER_OK = {"message": "If this email is new, your account has been created."}


@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, payload: UserCreate, db: DbSession) -> JSONResponse:
    """
    Register a new account.

    Returns 201 regardless of whether the email is new or already registered —
    the response body never distinguishes between the two cases to prevent
    account enumeration via the registration endpoint (AUTH-05).
    """
    try:
        await AuthService(db).register(payload.email, payload.full_name, payload.password)
    except AuthError as exc:
        match exc.code:
            case AuthError.EMAIL_EXISTS:
                pass  # silent — do not leak account existence
            case _:
                raise
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=_REGISTER_OK)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, payload: LoginRequest, db: DbSession) -> TokenResponse:
    try:
        return await AuthService(db).login(payload.email, payload.password)
    except AuthError as exc:
        match exc.code:
            case AuthError.INVALID_CREDENTIALS:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect email or password",
                )
            case AuthError.INACTIVE:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
                )
            case _:
                raise


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
async def refresh(request: Request, payload: RefreshRequest, db: DbSession) -> TokenResponse:
    try:
        return await AuthService(db).refresh(payload.refresh_token)
    except AuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: DbSession) -> None:
    """Revoke the submitted refresh token. Silent if already invalid."""
    await AuthService(db).logout(payload.refresh_token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
