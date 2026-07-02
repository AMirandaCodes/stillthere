"""
Shared pytest fixtures.

Fixture hierarchy:
  event_loop   (session-scoped) → one asyncio loop for all tests
  test_engine  (session-scoped) → creates all tables once per test run
  db_session   (function-scoped) → fresh session per test; TRUNCATE on teardown
  client       (function-scoped) → httpx AsyncClient wired to the test DB
  auth_headers (function-scoped) → registers a test user, returns Bearer headers

Rate limiting is disabled by setting RATE_LIMITS_ENABLED=false BEFORE any app
module is imported. The Limiter is a module-level singleton created at import
time; patching its _enabled attribute after import does not reliably take effect.

Event-loop design
-----------------
pytest-asyncio 0.23.x creates a NEW event loop per test function by default.
The session-scoped test_engine is created on a separate session-level loop.
When function-level tests use test_engine, asyncpg connections created on the
session loop would be reused on function loops — asyncpg rejects this ("Future
attached to a different loop") which manifests as a hang waiting for a PostgreSQL
socket that never replies.

Fix: override event_loop to be session-scoped so that test_engine, db_session,
and all test functions share ONE event loop. asyncpg connections are all on the
same loop; standard connection pooling is safe and reuses connections between
tests (no NullPool needed). The DeprecationWarning from overriding event_loop
is suppressed by filterwarnings = ignore::DeprecationWarning in pytest.ini.
"""
import asyncio
import os

# Must be set before any app import so rate_limiting.py reads it at module load time.
os.environ.setdefault("RATE_LIMITS_ENABLED", "false")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

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


@pytest.fixture(scope="session")
def event_loop():
    """Single asyncio event loop for the entire test session.

    Overrides the default function-scoped event_loop fixture from pytest-asyncio
    so that test_engine (session-scoped) and all test functions share one loop.
    Without this, session-scoped asyncpg connections created during test_engine
    setup land on the session loop; function-scoped tests then try to reuse those
    connections on their own function loops, which asyncpg rejects — causing the
    DB socket to wait forever and the test to hit --timeout.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncSession:
    """Each test gets its own session. Tables are TRUNCATE'd on teardown.

    TRUNCATE runs through the SAME session connection to avoid the asyncpg
    "another operation is in progress" error that occurs when a separate
    engine.connect() is used while the session connection is still being
    cleaned up by the pool.
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
