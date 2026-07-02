"""
Integration tests verifying that rate-limited endpoints return HTTP 429.

Redis is None in the test client (conftest sets app.state.redis = None), so
RateLimitService fails open and never blocks in normal tests.  These tests
patch RateLimitService.check_user / check_guest directly to simulate an
exhausted quota without needing a real Redis instance.
"""
import io
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.services.rate_limit_service import RateLimitService

_EXCEEDED = (False, 99, datetime.now(tz=timezone.utc) + timedelta(hours=2))
_CSV = b"Name,Company\nJane Doe,Acme Corp\n"


@pytest.mark.asyncio
class TestVerificationRateLimit:
    async def test_authenticated_user_gets_429_when_quota_exhausted(
        self, client: AsyncClient, auth_headers
    ):
        with patch.object(RateLimitService, "check_user", new=AsyncMock(return_value=_EXCEEDED)):
            response = await client.post(
                "/api/v1/verifications",
                json={"full_name": "Jane Doe", "company_name": "Acme"},
                headers=auth_headers,
            )
        assert response.status_code == 429
        detail = response.json()["detail"]
        assert "limit reached" in detail.lower()
        assert "per day" in detail

    async def test_guest_gets_429_when_quota_exhausted(self, client: AsyncClient):
        with patch.object(RateLimitService, "check_guest", new=AsyncMock(return_value=_EXCEEDED)):
            response = await client.post(
                "/api/v1/verifications",
                json={"full_name": "Jane Doe", "company_name": "Acme"},
            )
        assert response.status_code == 429
        detail = response.json()["detail"]
        assert "Sign up" in detail

    async def test_response_includes_reset_time(
        self, client: AsyncClient, auth_headers
    ):
        with patch.object(RateLimitService, "check_user", new=AsyncMock(return_value=_EXCEEDED)):
            response = await client.post(
                "/api/v1/verifications",
                json={"full_name": "Jane Doe", "company_name": "Acme"},
                headers=auth_headers,
            )
        assert response.status_code == 429
        assert "resets in" in response.json()["detail"]

    async def test_list_endpoint_not_rate_limited(
        self, client: AsyncClient, auth_headers
    ):
        """GET /verifications has no rate limit — must always return 200."""
        response = await client.get("/api/v1/verifications", headers=auth_headers)
        assert response.status_code == 200


@pytest.mark.asyncio
class TestBatchRateLimit:
    async def test_user_gets_429_when_batch_quota_exhausted(
        self, client: AsyncClient, auth_headers
    ):
        with patch.object(RateLimitService, "check_user", new=AsyncMock(return_value=_EXCEEDED)):
            response = await client.post(
                "/api/v1/batch/upload",
                files={"file": ("test.csv", io.BytesIO(_CSV), "text/csv")},
                headers=auth_headers,
            )
        assert response.status_code == 429
        detail = response.json()["detail"]
        assert "batch" in detail.lower()
        assert "per day" in detail

    async def test_batch_list_not_rate_limited(
        self, client: AsyncClient, auth_headers
    ):
        """GET /batch has no rate limit — must always return 200."""
        response = await client.get("/api/v1/batch", headers=auth_headers)
        assert response.status_code == 200
