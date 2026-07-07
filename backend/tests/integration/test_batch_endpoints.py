"""
Integration tests for the /batch HTTP endpoints.

Celery dispatch is mocked throughout so no worker is required.
The pipeline itself is tested separately in test_batch_pipeline.py.
"""
import io
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

_CSV = b"Name,Company,Email\nJane Doe,Test Corp,jane@test.com\n"
_CSV_NO_EMAIL = b"Name,Company\nJane Doe,Test Corp\n"


@pytest.fixture(autouse=True)
def mock_celery():
    """Prevent real Celery dispatch in every test in this module."""
    mock_task = MagicMock()
    mock_task.id = "mock-celery-task-id"
    with patch("app.tasks.batch_tasks.process_batch_job") as mock_proc:
        mock_proc.delay.return_value = mock_task
        yield mock_proc


def _csv_file(content: bytes = _CSV):
    return {"file": ("test.csv", io.BytesIO(content), "text/csv")}


@pytest.mark.asyncio
class TestUploadEndpoint:
    async def test_valid_csv_returns_202(self, client: AsyncClient, auth_headers):
        response = await client.post("/api/v1/batch/upload", files=_csv_file(), headers=auth_headers)
        assert response.status_code == 202

    async def test_response_contains_job_id_and_status(self, client: AsyncClient, auth_headers):
        response = await client.post("/api/v1/batch/upload", files=_csv_file(), headers=auth_headers)
        data = response.json()
        assert "id" in data
        assert data["status"] == "queued"
        assert data["total_records"] == 1

    async def test_csv_without_email_column_is_accepted(self, client: AsyncClient, auth_headers):
        response = await client.post(
            "/api/v1/batch/upload",
            files=_csv_file(_CSV_NO_EMAIL),
            headers=auth_headers,
        )
        assert response.status_code == 202

    async def test_missing_name_column_returns_400(self, client: AsyncClient, auth_headers):
        bad_csv = b"Company,Email\nTest Corp,jane@test.com"
        response = await client.post(
            "/api/v1/batch/upload", files=_csv_file(bad_csv), headers=auth_headers
        )
        assert response.status_code == 400
        assert "name" in response.json()["detail"].lower()

    async def test_missing_company_column_returns_400(self, client: AsyncClient, auth_headers):
        bad_csv = b"Name,Email\nJane Doe,jane@test.com"
        response = await client.post(
            "/api/v1/batch/upload", files=_csv_file(bad_csv), headers=auth_headers
        )
        assert response.status_code == 400
        assert "company" in response.json()["detail"].lower()

    async def test_upload_requires_auth(self, client: AsyncClient):
        response = await client.post("/api/v1/batch/upload", files=_csv_file())
        assert response.status_code == 401

    async def test_skipped_row_counted_at_upload(self, client: AsyncClient, auth_headers):
        csv_with_blank = b"Name,Company\nJane Doe,Test Corp\n,Test Corp\n"
        response = await client.post(
            "/api/v1/batch/upload",
            files=_csv_file(csv_with_blank),
            headers=auth_headers,
        )
        assert response.status_code == 202
        data = response.json()
        assert data["total_records"] == 2
        assert data["processed_records"] == 1  # blank-name row pre-counted


@pytest.mark.asyncio
class TestSingleJobEndpoints:
    async def _upload(self, client, auth_headers) -> str:
        r = await client.post("/api/v1/batch/upload", files=_csv_file(), headers=auth_headers)
        return r.json()["id"]

    async def test_get_job_returns_200(self, client: AsyncClient, auth_headers):
        job_id = await self._upload(client, auth_headers)
        r = await client.get(f"/api/v1/batch/{job_id}", headers=auth_headers)
        assert r.status_code == 200

    async def test_get_unknown_job_returns_404(self, client: AsyncClient, auth_headers):
        r = await client.get(
            "/api/v1/batch/00000000-0000-0000-0000-000000000000", headers=auth_headers
        )
        assert r.status_code == 404

    async def test_get_results_returns_paginated(self, client: AsyncClient, auth_headers):
        job_id = await self._upload(client, auth_headers)
        r = await client.get(f"/api/v1/batch/{job_id}/results", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "total" in data

    async def test_export_non_complete_job_returns_400(self, client: AsyncClient, auth_headers):
        job_id = await self._upload(client, auth_headers)
        r = await client.get(f"/api/v1/batch/{job_id}/export", headers=auth_headers)
        assert r.status_code == 400
        assert "not yet complete" in r.json()["detail"].lower()


@pytest.mark.asyncio
class TestListBatchJobs:
    async def test_list_returns_paginated_response(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get("/api/v1/batch", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    async def test_list_includes_own_job(self, client: AsyncClient, auth_headers):
        await client.post("/api/v1/batch/upload", files=_csv_file(), headers=auth_headers)
        response = await client.get("/api/v1/batch", headers=auth_headers)
        assert response.json()["total"] == 1

    async def test_list_excludes_other_users_jobs(
        self, client: AsyncClient, auth_headers
    ):
        """A batch job uploaded by another user must not appear in the current user's list."""
        await client.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "full_name": "Other User", "password": "Otherpass1"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "other@example.com", "password": "Otherpass1"},
        )
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        await client.post("/api/v1/batch/upload", files=_csv_file(), headers=other_headers)

        response = await client.get("/api/v1/batch", headers=auth_headers)
        assert response.json()["total"] == 0

    async def test_list_requires_auth(self, client: AsyncClient):
        response = await client.get("/api/v1/batch")
        assert response.status_code == 401
