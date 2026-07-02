"""
Shared pytest fixtures.

Fixture hierarchy:
  test_engine  (session-scoped) → creates all tables once per test run
  db_session   (function-scoped) → provides a fresh session per test; TRUNCATE via same session
  client       (function-scoped) → httpx AsyncClient wired to the test DB
  auth_headers (function-scoped) → registers a test user, returns Bearer headers

Rate limiting is disabled by setting RATE_LIMITS_ENABLED=false BEFORE any app module is
imported. The Limiter is a module-level singleton created at import time; patching its
_enabled attribute after import does not reliably take effect because pytest-asyncio runs
async fixture setup before sync autouse wrappers activate.

NullPool was removed: it caused every DB operation to open a new TCP connection to
PostgreSQL (no reuse), adding ~100ms per connection × hundreds of connections = minutes of
overhead across 60+ tests. Standard pooling reuses connections; the asyncpg "another
operation is in progress" error that originally prompted NullPool was caused by savepoints
(since removed — we now use TRUNCATE cleanup instead).

TRUNCATE runs through the same db_session connection (not a separate connection) to avoid
any pool-reuse race conditions.
"""
import os

# Must be set before any app import so rate_limiting.py reads it at module load time.
os.environ.setdefault("RATE_LIMITS_ENABLED", "false")

import pytest_asyncio  # noqa: E402
from httpx import AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.db.registry  # noqa: F401, E402 — registers all ORM models
from app.api.deps import get_db  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import app  # noqa: E402

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://cvp_user:cvp_password@localhost:5432/contact_verification_test",
)

_TEST_USER = {
    "email": "testuser@example.com",
    "full_name": "Test User",
    "password": "testpassword123",
}


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncSession:
    """Each test gets its own session. All table data is truncated after the test runs.

    TRUNCATE runs through the same session (same pooled connection) to avoid
    the asyncpg "another operation is in progress" race that occurs when a
    separate connection tries to run TRUNCATE while the pool still considers
    the previous connection active.
    """
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
        table_names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await session.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
        await session.commit()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """HTTP client wired to the test database via dependency override."""

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.state.redis = None

    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a test user and return Bearer auth headers."""
    reg = await client.post("/api/v1/auth/register", json=_TEST_USER)
    assert reg.status_code in (200, 201), f"Registration failed: {reg.text}"

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": _TEST_USER["email"], "password": _TEST_USER["password"]},
    )
    assert login.status_code == 200, f"Login failed: {login.text}"

    return {"Authorization": f"Bearer {login.json()['access_token']}"}
