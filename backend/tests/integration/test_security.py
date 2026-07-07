"""
Security-focused integration tests.

Covers input validation boundaries and authentication edge cases that sit
outside the primary happy-path tests.
"""
import io
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def mock_celery():
    mock_task = MagicMock()
    mock_task.id = "mock-celery-task-id"
    with patch("app.tasks.batch_tasks.process_batch_job") as mock_proc:
        mock_proc.delay.return_value = mock_task
        yield mock_proc


# ── Batch upload — size limits ─────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBatchSizeLimits:
    async def test_oversized_csv_row_count_returns_400(
        self, client: AsyncClient, auth_headers
    ):
        """51 data rows exceeds MAX_BATCH_SIZE=50 → 400 with informative message."""
        rows = "Name,Company\n" + "\n".join(f"Person {i},Acme Ltd" for i in range(51))
        resp = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("big.csv", io.BytesIO(rows.encode()), "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "maximum" in detail or "limit" in detail or "50" in detail

    async def test_file_over_5mb_returns_400(
        self, client: AsyncClient, auth_headers
    ):
        """A 5 MB + 1 byte upload must be rejected before parsing."""
        # Build a file slightly larger than _MAX_CSV_BYTES (5 * 1024 * 1024)
        header = b"Name,Company\n"
        padding = b"A" * (5 * 1024 * 1024)  # exactly 5 MB of garbage CSV content
        large_content = header + padding      # just over 5 MB when combined
        resp = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("large.csv", io.BytesIO(large_content), "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "5 mb" in resp.json()["detail"].lower() or "size" in resp.json()["detail"].lower()

    async def test_exact_max_batch_size_is_accepted(
        self, client: AsyncClient, auth_headers
    ):
        """Exactly MAX_BATCH_SIZE=50 rows must be accepted (boundary check)."""
        rows = "Name,Company\n" + "\n".join(f"Person {i},Acme Ltd" for i in range(50))
        resp = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("ok.csv", io.BytesIO(rows.encode()), "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 202

    async def test_non_utf8_file_returns_400(
        self, client: AsyncClient, auth_headers
    ):
        """A Latin-1 encoded file that isn't valid UTF-8 must return 400."""
        latin1_bytes = "Name,Company\nJosé García,Acme".encode("latin-1")
        resp = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("latin.csv", io.BytesIO(latin1_bytes), "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "utf-8" in resp.json()["detail"].lower()


# ── Authentication — token edge cases ─────────────────────────────────────────

@pytest.mark.asyncio
class TestAuthTokenSecurity:
    async def test_missing_auth_header_returns_401(self, client: AsyncClient):
        resp = await client.get("/api/v1/verifications")
        assert resp.status_code == 401

    async def test_malformed_bearer_returns_401(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/verifications",
            headers={"Authorization": "NotBearer sometoken"},
        )
        assert resp.status_code == 401

    async def test_garbage_token_returns_401(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/verifications",
            headers={"Authorization": "Bearer thisisnotavalidjwt"},
        )
        assert resp.status_code == 401

    async def test_batch_list_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/batch")
        assert resp.status_code == 401

    async def test_contacts_list_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/contacts")
        assert resp.status_code == 401

    async def test_companies_list_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/companies")
        assert resp.status_code == 401
