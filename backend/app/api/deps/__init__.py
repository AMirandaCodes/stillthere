"""
FastAPI dependency injection — re-exports all deps so existing
`from app.api.deps import X` imports continue to work unchanged.
"""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db  # re-exported for routes/tests that import it from here

from app.api.deps.auth import (
    CurrentAdmin,
    CurrentUser,
    OptionalUser,
    get_current_admin,
    get_current_user,
    get_optional_user,
)
from app.api.deps.cache import CacheDep, get_cache
from app.api.deps.pagination import PaginationDep, PaginationParams
from app.api.deps.rate_limit import BatchRateLimit, VerificationRateLimit

# ── Typed aliases shared across all deps ──────────────────────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db)]

__all__ = [
    # common
    "SettingsDep",
    "DbSession",
    "get_db",
    # auth
    "CurrentAdmin",
    "CurrentUser",
    "OptionalUser",
    "get_current_admin",
    "get_current_user",
    "get_optional_user",
    # cache
    "CacheDep",
    "get_cache",
    # pagination
    "PaginationDep",
    "PaginationParams",
    # rate limiting
    "BatchRateLimit",
    "VerificationRateLimit",
]
