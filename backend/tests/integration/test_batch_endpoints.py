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
            json={"email": "other@example.com", "full_name": "Other User", "password": "otherpassword"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "other@example.com", "password": "otherpassword"},
        )
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        await client.post("/api/v1/batch/upload", files=_csv_file(), headers=other_headers)

        response = await client.get("/api/v1/batch", headers=auth_headers)
        assert response.json()["total"] == 0

    async def test_list_requires_auth(self, client: AsyncClient):
        response = await client.get("/api/v1/batch")
        assert response.status_code == 401
