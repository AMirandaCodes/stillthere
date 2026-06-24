"""
Integration tests for the /contacts and /companies endpoints.
"""
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def mock_celery():
    mock_task = MagicMock()
    mock_task.id = "mock-celery-task-id"
    with patch("app.services.verification_service.run_verification") as m:
        m.delay.return_value = mock_task
        yield m


async def _create_verification(client, auth_headers, name, company, email=None):
    payload = {"full_name": name, "company_name": company}
    if email:
        payload["work_email"] = email
    await client.post("/api/v1/verifications", json=payload, headers=auth_headers)


@pytest.mark.asyncio
class TestContactsEndpoints:
    async def test_list_contacts_returns_paginated(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get("/api/v1/contacts", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data and "total" in data

    async def test_list_contacts_requires_auth(self, client: AsyncClient):
        assert (await client.get("/api/v1/contacts")).status_code == 401

    async def test_contact_has_verification_count(
        self, client: AsyncClient, auth_headers
    ):
        await _create_verification(
            client, auth_headers, "Count Test", "Corp A", "count@corp.com"
        )
        await _create_verification(
            client, auth_headers, "Count Test", "Corp B", "count@corp.com"
        )
        response = await client.get("/api/v1/contacts", headers=auth_headers)
        contact = next(
            (c for c in response.json()["items"] if c["email"] == "count@corp.com"),
            None,
        )
        assert contact is not None
        assert contact["total_verifications"] >= 2

    async def test_get_contact_by_id(self, client: AsyncClient, auth_headers):
        await _create_verification(
            client, auth_headers, "Detail Person", "Detail Corp", "detail@corp.com"
        )
        contacts = await client.get("/api/v1/contacts", headers=auth_headers)
        contact_id = next(
            c["id"]
            for c in contacts.json()["items"]
            if c["email"] == "detail@corp.com"
        )
        detail = await client.get(
            f"/api/v1/contacts/{contact_id}", headers=auth_headers
        )
        assert detail.status_code == 200
        assert detail.json()["full_name"] == "Detail Person"

    async def test_get_contact_404(self, client: AsyncClient, auth_headers):
        fake = "00000000-0000-0000-0000-000000000001"
        assert (
            await client.get(f"/api/v1/contacts/{fake}", headers=auth_headers)
        ).status_code == 404

    async def test_search_contacts_by_name(self, client: AsyncClient, auth_headers):
        await _create_verification(
            client, auth_headers, "Searchable Name", "Some Corp"
        )
        response = await client.get(
            "/api/v1/contacts?q=searchable", headers=auth_headers
        )
        assert response.status_code == 200
        names = [c["full_name"] for c in response.json()["items"]]
        assert any("Searchable" in n for n in names)


@pytest.mark.asyncio
class TestCompaniesEndpoints:
    async def test_list_companies_returns_paginated(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get("/api/v1/companies", headers=auth_headers)
        assert response.status_code == 200
        assert "items" in response.json()

    async def test_list_companies_requires_auth(self, client: AsyncClient):
        assert (await client.get("/api/v1/companies")).status_code == 401

    async def test_get_company_by_id(self, client: AsyncClient, auth_headers):
        await _create_verification(
            client, auth_headers, "Employee One", "Detail Company Ltd"
        )
        companies = await client.get("/api/v1/companies", headers=auth_headers)
        company_id = next(
            c["id"]
            for c in companies.json()["items"]
            if c["name"] == "Detail Company Ltd"
        )
        detail = await client.get(
            f"/api/v1/companies/{company_id}", headers=auth_headers
        )
        assert detail.status_code == 200
        assert detail.json()["name"] == "Detail Company Ltd"

    async def test_get_company_404(self, client: AsyncClient, auth_headers):
        fake = "00000000-0000-0000-0000-000000000002"
        assert (
            await client.get(f"/api/v1/companies/{fake}", headers=auth_headers)
        ).status_code == 404
