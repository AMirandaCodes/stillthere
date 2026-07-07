"""
Integration tests for the /auth endpoints.
Requires a running PostgreSQL instance (uses the test DB from conftest.py).
"""
import asyncio

import pytest
from httpx import AsyncClient

# Passwords used throughout — all meet the SEC-10 policy (≥8 chars, ≥1 letter, ≥1 digit)
_PWD = "Securepass1"


@pytest.mark.asyncio
class TestRegister:
    async def test_register_success(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "alice@example.com", "full_name": "Alice Smith", "password": _PWD},
        )
        assert response.status_code == 201
        data = response.json()
        assert "message" in data
        assert "hashed_password" not in data

    async def test_register_duplicate_email_returns_201(self, client: AsyncClient):
        # Duplicate registration must return 201 (same as new) — no enumeration (AUTH-05)
        payload = {"email": "bob@example.com", "full_name": "Bob Jones", "password": _PWD}
        first = await client.post("/api/v1/auth/register", json=payload)
        second = await client.post("/api/v1/auth/register", json=payload)
        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json() == first.json()

    async def test_register_short_password_returns_422(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "carol@example.com", "full_name": "Carol", "password": "short"},
        )
        assert response.status_code == 422

    async def test_register_no_digit_returns_422(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "carol2@example.com", "full_name": "Carol", "password": "alllowercase"},
        )
        assert response.status_code == 422

    async def test_register_invalid_email_returns_422(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "full_name": "Dan", "password": _PWD},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
class TestLogin:
    async def test_login_returns_tokens(self, client: AsyncClient):
        await client.post(
            "/api/v1/auth/register",
            json={"email": "eve@example.com", "full_name": "Eve", "password": _PWD},
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "eve@example.com", "password": _PWD},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password_returns_401(self, client: AsyncClient):
        await client.post(
            "/api/v1/auth/register",
            json={"email": "frank@example.com", "full_name": "Frank", "password": _PWD},
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "frank@example.com", "password": "TotallyWrong99"},
        )
        assert response.status_code == 401

    async def test_login_unknown_email_returns_401(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "anything"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
class TestTokenRefresh:
    async def _register_and_login(self, client: AsyncClient, email: str) -> dict:
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "full_name": "Test User", "password": _PWD},
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": _PWD},
        )
        return response.json()

    async def test_refresh_returns_new_tokens(self, client: AsyncClient):
        tokens = await self._register_and_login(client, "grace@example.com")
        await asyncio.sleep(1)  # ensure different iat so JWT access tokens differ
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert response.status_code == 200
        new_tokens = response.json()
        assert new_tokens["access_token"] != tokens["access_token"]
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

    async def test_refresh_token_cannot_be_reused(self, client: AsyncClient):
        tokens = await self._register_and_login(client, "henry@example.com")
        await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        # Second use of the same token must fail
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert response.status_code == 401

    async def test_refresh_reuse_revokes_all_sessions(self, client: AsyncClient):
        """Reusing a rotated RT triggers family revocation — the new RT is also killed."""
        tokens = await self._register_and_login(client, "ivan@example.com")
        # First rotation (legitimate use)
        rotated = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        new_rt = rotated.json()["refresh_token"]
        # Attacker replays the old (now-revoked) token — should trigger family revocation
        await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        # The new RT (which the attacker would hold) must now be invalid too
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": new_rt},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
class TestGetMe:
    async def test_me_returns_current_user(self, client: AsyncClient):
        await client.post(
            "/api/v1/auth/register",
            json={"email": "iris@example.com", "full_name": "Iris", "password": _PWD},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "iris@example.com", "password": _PWD},
        )
        token = login.json()["access_token"]
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["email"] == "iris@example.com"

    async def test_me_without_token_returns_401(self, client: AsyncClient):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401
