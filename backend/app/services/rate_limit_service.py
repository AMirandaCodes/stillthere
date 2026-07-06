"""
RateLimitService — per-user and per-IP daily usage counters backed by Redis.

Key format:
  stillthere:rl:user:{user_id}:{resource}:{YYYY-MM-DD}  — authenticated users
  stillthere:rl:guest:{ip_hash}:{resource}:{YYYY-MM-DD} — unauthenticated visitors

Counters are set to expire at UTC midnight via EXPIREAT so keys clean up
automatically and the window is always a calendar day.

Fail-open: if Redis is unavailable, all checks pass.  This matches the
CacheService contract — the feature degrades gracefully rather than blocking users.
"""
from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None  # type: ignore[assignment,misc]

_PREFIX = "stillthere:rl"


def _tomorrow_midnight_utc() -> datetime:
    tomorrow = date.today() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=timezone.utc)


def _today_str() -> str:
    return date.today().isoformat()  # e.g. "2025-01-15"


class RateLimitService:
    def __init__(self, redis: "Redis | None") -> None:
        self._redis = redis

    async def _check_and_increment(self, key: str, limit: int) -> tuple[bool, int, datetime]:
        """
        Atomically increment *key* and return (allowed, new_count, reset_at).

        Uses a Redis pipeline: INCR → EXPIREAT (next UTC midnight).
        Returns (True, 0, reset_at) if Redis is unavailable — fail open.
        """
        reset_at = _tomorrow_midnight_utc()
        if not self._redis:
            return True, 0, reset_at
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expireat(key, int(reset_at.timestamp()))
            results = await pipe.execute()
            count: int = results[0]
            return count <= limit, count, reset_at
        except Exception as exc:
            logger.warning("rate_limit.check failed", key=key, error=str(exc))
            return True, 0, reset_at  # fail open

    async def check_user(
        self, user_id: UUID, resource: str, limit: int
    ) -> tuple[bool, int, datetime]:
        key = f"{_PREFIX}:user:{user_id}:{resource}:{_today_str()}"
        return await self._check_and_increment(key, limit)

    async def check_guest(
        self, ip: str, resource: str, limit: int
    ) -> tuple[bool, int, datetime]:
        # Hash the IP so raw addresses are not stored in Redis keys.
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
        key = f"{_PREFIX}:guest:{ip_hash}:{resource}:{_today_str()}"
        return await self._check_and_increment(key, limit)

    def format_reset_message(self, reset_at: datetime) -> str:
        """Human-readable 'resets in Xh Ym' string based on time until reset_at."""
        total = max(0.0, (reset_at - datetime.now(tz=timezone.utc)).total_seconds())
        minutes_total = math.ceil(total / 60)
        hours, minutes = divmod(minutes_total, 60)
        if hours > 0:
            return f"resets in {hours}h {minutes}m"
        return f"resets in {minutes}m"
