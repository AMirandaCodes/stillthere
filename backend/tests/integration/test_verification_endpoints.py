"""
Integration tests for the /verifications endpoints.

The Celery task (run_verification) is mocked so tests don't need a running
worker.  This isolates Phase 3 logic (route → service → DB) from Phase 4
(the pipeline itself).
"""
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

_VALID_PAYLOAD = {
    "full_name": "John Smith",
    "company_name": "Acme Ltd",
    "work_email": "john.smith@acme.com",
}

_MINIMAL_PAYLOAD = {
    "full_name": "Jane Doe",
    "company_name": "XYZ Corp",
}


@pytest.fixture(autouse=True)
def mock_celery(monkeypatch):
    """Prevent real Celery dispatch in every test in this module."""
    mock_task = MagicMock()
    mock_task.id = "mock-celery-task-id"
    with patch(
        "app.services.verification_service.run_verification"
    ) as mock_run:
        mock_run.delay.return_value = mock_task
        yield mock_run


@pytest.mark.asyncio
class TestSubmitVerification:
    async def test_submit_returns_202(self, client: AsyncClient, auth_headers):
        response = await client.post(
            "/api/v1/verifications", json=_VALID_PAYLOAD, headers=auth_headers
        )
        assert response.status_code == 202

    async def test_submit_returns_job_ids(self, client: AsyncClient, auth_headers):
        response = await client.post(
            "/api/v1/verifications", json=_VALID_PAYLOAD, headers=auth_headers
        )
        data = response.json()
        assert "search_id" in data
        assert "verification_id" in data
        assert data["status"] == "pending"

    async def test_submit_without_email(self, client: AsyncClient, auth_headers):
        response = await client.post(
            "/api/v1/verifications", json=_MINIMAL_PAYLOAD, headers=auth_headers
        )
        assert response.status_code == 202

    async def test_submit_requires_auth(self, client: AsyncClient):
        response = await client.post("/api/v1/verifications", json=_VALID_PAYLOAD)
        assert response.status_code == 401

    async def test_submit_empty_name_rejected(self, client: AsyncClient, auth_headers):
        response = await client.post(
            "/api/v1/verifications",
            json={"full_name": "", "company_name": "Acme Ltd"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_duplicate_email_reuses_contact(
        self, client: AsyncClient, auth_headers
    ):
        """Two verifications for the same email must not create two Contact rows."""
        await client.post(
            "/api/v1/verifications", json=_VALID_PAYLOAD, headers=auth_headers
        )
        r2 = await client.post(
            "/api/v1/verifications",
            json={**_VALID_PAYLOAD, "company_name": "Different Corp"},
            headers=auth_headers,
        )
        assert r2.status_code == 202

        # Check contacts list — should have exactly one entry for this email
        contacts = await client.get("/api/v1/contacts", headers=auth_headers)
        emails = [c["email"] for c in contacts.json()["items"] if c["email"]]
        assert emails.count("john.smith@acme.com") == 1

    async def test_duplicate_company_reuses_company(
        self, client: AsyncClient, auth_headers
    ):
        """Two verifications for the same company must not create two Company rows."""
        await client.post(
            "/api/v1/verifications", json=_VALID_PAYLOAD, headers=auth_headers
        )
        await client.post(
            "/api/v1/verifications",
            json={"full_name": "Another Person", "company_name": "Acme Ltd"},
            headers=auth_headers,
        )
        companies = await client.get("/api/v1/companies", headers=auth_headers)
        names = [c["name"] for c in companies.json()["items"]]
        assert names.count("Acme Ltd") == 1


@pytest.mark.asyncio
class TestGetVerification:
    async def _submit(self, client, auth_headers) -> str:
        r = await client.post(
            "/api/v1/verifications", json=_VALID_PAYLOAD, headers=auth_headers
        )
        return r.json()["verification_id"]

    async def test_get_returns_200(self, client: AsyncClient, auth_headers):
        vid = await self._submit(client, auth_headers)
        response = await client.get(
            f"/api/v1/verifications/{vid}", headers=auth_headers
        )
        assert response.status_code == 200

    async def test_get_contains_report_fields(self, client: AsyncClient, auth_headers):
        vid = await self._submit(client, auth_headers)
        data = (
            await client.get(f"/api/v1/verifications/{vid}", headers=auth_headers)
        ).json()
        for field in (
            "person_found", "appears_associated", "found_on_website",
            "company_active", "email_match", "confidence_score",
            "confidence_level", "evidence_sources", "useful_links",
        ):
            assert field in data, f"Missing field: {field}"

    async def test_all_tristate_fields_default_to_unclear(
        self, client: AsyncClient, auth_headers
    ):
        vid = await self._submit(client, auth_headers)
        data = (
            await client.get(f"/api/v1/verifications/{vid}", headers=auth_headers)
        ).json()
        for field in (
            "person_found", "appears_associated", "found_on_website",
            "company_active", "email_match",
        ):
            assert data[field] == "unclear", f"{field} should default to 'unclear'"

    async def test_get_reflects_contact_and_company(
        self, client: AsyncClient, auth_headers
    ):
        vid = await self._submit(client, auth_headers)
        data = (
            await client.get(f"/api/v1/verifications/{vid}", headers=auth_headers)
        ).json()
        assert data["full_name"] == "John Smith"
        assert data["company_name"] == "Acme Ltd"
        assert data["work_email"] == "john.smith@acme.com"

    async def test_get_unknown_id_returns_404(
        self, client: AsyncClient, auth_headers
    ):
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await client.get(
            f"/api/v1/verifications/{fake_id}", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_get_requires_auth(self, client: AsyncClient, auth_headers):
        vid = await self._submit(client, auth_headers)
        response = await client.get(f"/api/v1/verifications/{vid}")
        assert response.status_code == 401


@pytest.mark.asyncio
class TestListVerifications:
    async def test_list_returns_paginated_response(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get("/api/v1/verifications", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "total_pages" in data

    async def test_list_includes_submitted_verification(
        self, client: AsyncClient, auth_headers
    ):
        await client.post(
            "/api/v1/verifications",
            json={"full_name": "List Test", "company_name": "List Corp"},
            headers=auth_headers,
        )
        response = await client.get("/api/v1/verifications", headers=auth_headers)
        names = [v["full_name"] for v in response.json()["items"]]
        assert "List Test" in names

    async def test_list_requires_auth(self, client: AsyncClient):
        response = await client.get("/api/v1/verifications")
        assert response.status_code == 401

    async def test_pagination_params_accepted(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get(
            "/api/v1/verifications?page=1&page_size=5", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["page_size"] == 5
