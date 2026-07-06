from typing import Annotated

from fastapi import Depends, Request

from app.services.cache_service import CacheService


async def get_cache(request: Request) -> CacheService:
    """
    Return a CacheService backed by the app-level Redis connection pool.
    Falls back to a no-op CacheService if Redis was not initialised.
    """
    redis = getattr(request.app.state, "redis", None)
    return CacheService(redis)


CacheDep = Annotated[CacheService, Depends(get_cache)]
