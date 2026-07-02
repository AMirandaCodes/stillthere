"""Unit tests for RateLimitService."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.rate_limit_service import RateLimitService


def _make_redis(pipeline_results: list):
    """Return a mock Redis whose pipeline().execute() returns pipeline_results."""
    mock_redis = MagicMock()
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=pipeline_results)
    mock_redis.pipeline.return_value = mock_pipe
    return mock_redis, mock_pipe


class TestRateLimitServiceFailOpen:
    @pytest.mark.asyncio
    async def test_no_redis_user_always_allowed(self):
        rl = RateLimitService(None)
        allowed, count, _ = await rl.check_user(uuid4(), "verifications", 5)
        assert allowed is True
        assert count == 0

    @pytest.mark.asyncio
    async def test_no_redis_guest_always_allowed(self):
        rl = RateLimitService(None)
        allowed, count, _ = await rl.check_guest("1.2.3.4", "verifications", 1)
        assert allowed is True
        assert count == 0

    @pytest.mark.asyncio
    async def test_redis_error_fails_open(self):
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=Exception("Connection refused"))
        mock_redis.pipeline.return_value = mock_pipe

        rl = RateLimitService(mock_redis)
        allowed, count, _ = await rl.check_user(uuid4(), "verifications", 5)
        assert allowed is True
        assert count == 0


class TestRateLimitServiceCounting:
    @pytest.mark.asyncio
    async def test_allows_first_use(self):
        mock_redis, _ = _make_redis([1, True])  # count=1
        rl = RateLimitService(mock_redis)
        allowed, count, _ = await rl.check_user(uuid4(), "verifications", 5)
        assert allowed is True
        assert count == 1

    @pytest.mark.asyncio
    async def test_allows_at_limit(self):
        mock_redis, _ = _make_redis([5, True])  # count=5, limit=5
        rl = RateLimitService(mock_redis)
        allowed, count, _ = await rl.check_user(uuid4(), "verifications", 5)
        assert allowed is True
        assert count == 5

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self):
        mock_redis, _ = _make_redis([6, True])  # count=6 > limit=5
        rl = RateLimitService(mock_redis)
        allowed, count, _ = await rl.check_user(uuid4(), "verifications", 5)
        assert allowed is False
        assert count == 6

    @pytest.mark.asyncio
    async def test_guest_limit_of_one(self):
        mock_redis, mock_pipe = _make_redis([2, True])  # second guest attempt
        rl = RateLimitService(mock_redis)
        allowed, _, _ = await rl.check_guest("1.2.3.4", "verifications", 1)
        assert allowed is False

    @pytest.mark.asyncio
    async def test_guest_key_does_not_contain_raw_ip(self):
        mock_redis, mock_pipe = _make_redis([1, True])
        rl = RateLimitService(mock_redis)
        await rl.check_guest("192.168.99.1", "verifications", 1)
        key_used = mock_pipe.incr.call_args[0][0]
        assert "192.168.99.1" not in key_used

    @pytest.mark.asyncio
    async def test_uses_expireat_with_future_timestamp(self):
        mock_redis, mock_pipe = _make_redis([1, True])
        rl = RateLimitService(mock_redis)
        _, _, reset_at = await rl.check_user(uuid4(), "verifications", 5)
        expireat_ts = mock_pipe.expireat.call_args[0][1]
        assert expireat_ts > datetime.now(tz=timezone.utc).timestamp()
        assert reset_at > datetime.now(tz=timezone.utc)

    @pytest.mark.asyncio
    async def test_reset_at_is_next_midnight_utc(self):
        mock_redis, _ = _make_redis([1, True])
        rl = RateLimitService(mock_redis)
        _, _, reset_at = await rl.check_user(uuid4(), "verifications", 5)
        assert reset_at.hour == 0
        assert reset_at.minute == 0
        assert reset_at.second == 0
        assert reset_at > datetime.now(tz=timezone.utc)


class TestFormatResetMessage:
    def test_hours_and_minutes(self):
        rl = RateLimitService(None)
        reset_at = datetime.now(tz=timezone.utc) + timedelta(hours=3, minutes=25)
        msg = rl.format_reset_message(reset_at)
        assert "3h" in msg
        assert "25m" in msg

    def test_minutes_only(self):
        rl = RateLimitService(None)
        reset_at = datetime.now(tz=timezone.utc) + timedelta(minutes=47)
        msg = rl.format_reset_message(reset_at)
        assert "h" not in msg
        assert "47m" in msg

    def test_past_reset_at_returns_zero(self):
        rl = RateLimitService(None)
        reset_at = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
        msg = rl.format_reset_message(reset_at)
        assert "0m" in msg
