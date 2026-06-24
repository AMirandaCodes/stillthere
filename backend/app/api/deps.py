"""
FastAPI dependency injection definitions.
All shared dependencies live here: DB session, current user, pagination, cache.
"""
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_access_token
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.cache_service import CacheService

# ── Typed aliases ──────────────────────────────────────────────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db)]

# ── Pagination ─────────────────────────────────────────────────────────────────

class PaginationParams:
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number (1-based)"),
        page_size: int = Query(20, ge=1, le=100, description="Results per page"),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


PaginationDep = Annotated[PaginationParams, Depends()]

# ── Authentication ─────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract and validate the Bearer JWT, then load the user from the database.
    Raises HTTP 401 if the token is missing, invalid, or expired.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")

    user = await UserRepository(db).get_by_id(UUID(user_id_str))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

# ── Cache ──────────────────────────────────────────────────────────────────────

async def get_cache(request: Request) -> CacheService:
    """
    Return a CacheService backed by the app-level Redis connection pool.
    Falls back to a no-op CacheService if Redis was not initialised.
    """
    redis = getattr(request.app.state, "redis", None)
    return CacheService(redis)


CacheDep = Annotated[CacheService, Depends(get_cache)]
