"""
CacheService — Redis-backed cache with graceful degradation.

All public methods are safe to call when Redis is unavailable: they log a warning
and return None / do nothing rather than raising.  This means the verification
pipeline always runs; it just skips the cache on Redis failure.

Cache key conventions (prefix: 'cvp:'):
  cvp:company:{normalized_name}:profile   — domain, website            (TTL 24 h)
  cvp:company:{normalized_name}:active    — tri-state active status    (TTL  1 h)
  cvp:search:{query_hash}:results         — raw Serper results         (TTL 30 min)
"""
import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None  # type: ignore[assignment,misc]


class CacheService:
    TTL_COMPANY_PROFILE = 86_400       # 24 hours — company info is stable
    TTL_COMPANY_ACTIVE_STATUS = 3_600  # 1 hour  — trading status can change

    TTL_SEARCH_RESULTS = 1_800           # 30 minutes — search results are reusable short-term

    _KEY_COMPANY_PROFILE = "cvp:company:{}:profile"
    _KEY_COMPANY_ACTIVE = "cvp:company:{}:active"
    _KEY_SEARCH_RESULTS = "cvp:search:{}:results"

    def __init__(self, redis: "Redis | None") -> None:
        self._redis = redis

    # ── Generic primitives ─────────────────────────────────────────────────────

    async def get(self, key: str) -> str | None:
        if not self._redis:
            return None
        try:
            return await self._redis.get(key)
        except Exception as exc:
            logger.warning("cache.get failed", key=key, error=str(exc))
            return None

    async def set(self, key: str, value: str, ttl: int) -> None:
        if not self._redis:
            return
        try:
            await self._redis.setex(key, ttl, value)
        except Exception as exc:
            logger.warning("cache.set failed", key=key, error=str(exc))

    async def delete(self, key: str) -> None:
        if not self._redis:
            return
        try:
            await self._redis.delete(key)
        except Exception as exc:
            logger.warning("cache.delete failed", key=key, error=str(exc))

    # ── Company-level helpers ──────────────────────────────────────────────────

    async def get_company_profile(self, normalized_name: str) -> dict[str, Any] | None:
        """
        Return cached company profile or None on a cache miss.
        Called by VerificationService before issuing a Serper search for the company.
        """
        raw = await self.get(self._KEY_COMPANY_PROFILE.format(normalized_name))
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    async def set_company_profile(self, normalized_name: str, profile: dict[str, Any]) -> None:
        await self.set(
            self._KEY_COMPANY_PROFILE.format(normalized_name),
            json.dumps(profile),
            self.TTL_COMPANY_PROFILE,
        )

    async def get_company_active_status(self, normalized_name: str) -> str | None:
        """
        Return cached tri-state active status ('yes'/'no'/'unclear') or None.
        Short TTL because trading status can change without warning.
        """
        return await self.get(self._KEY_COMPANY_ACTIVE.format(normalized_name))

    async def set_company_active_status(self, normalized_name: str, status: str) -> None:
        await self.set(
            self._KEY_COMPANY_ACTIVE.format(normalized_name),
            status,
            self.TTL_COMPANY_ACTIVE_STATUS,
        )

    async def invalidate_company(self, normalized_name: str) -> None:
        """
        Purge all cached data for a company.
        Called when a fresh verification finds contradictory information.
        """
        await self.delete(self._KEY_COMPANY_PROFILE.format(normalized_name))
        await self.delete(self._KEY_COMPANY_ACTIVE.format(normalized_name))

    # ── Search result helpers ─────────────────────────────────────────────────

    async def get_search_results(self, query_hash: str) -> dict | None:
        """
        Return cached Serper results for a query hash, or None on miss.
        The hash is the first 32 hex chars of SHA-256 of the query string
        (use SearchService.query_cache_key to generate the full key).
        """
        raw = await self.get(self._KEY_SEARCH_RESULTS.format(query_hash))
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    async def set_search_results(self, query_hash: str, results: dict) -> None:
        """Cache raw Serper response for 30 minutes."""
        await self.set(
            self._KEY_SEARCH_RESULTS.format(query_hash),
            json.dumps(results),
            self.TTL_SEARCH_RESULTS,
        )

    async def invalidate_search(self, query_hash: str) -> None:
        await self.delete(self._KEY_SEARCH_RESULTS.format(query_hash))
