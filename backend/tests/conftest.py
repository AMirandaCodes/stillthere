"""
Shared pytest fixtures.

Fixture hierarchy:
  test_engine (session-scoped) → creates all tables once per test run
  db_session  (function-scoped) → wraps each test in a rolled-back transaction
  client      (function-scoped) → httpx AsyncClient wired to the test DB
  auth_headers (function-scoped) → registers a user and returns Bearer headers
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db
from app.db.base import Base
from app.main import app

TEST_DATABASE_URL = (
    "postgresql+asyncpg://cvp_user:cvp_password@localhost:5432/contact_verification_test"
)

_TEST_USER = {
    "email": "testuser@example.com",
    "full_name": "Test User",
    "password": "testpassword123",
}


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
    """Each test gets a session that is rolled back on teardown — DB stays clean."""
    SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """HTTP client wired to the test database via dependency override."""

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    # Disable Redis in tests: cache falls back gracefully when app.state.redis is None
    app.state.redis = None

    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a test user and return Bearer auth headers."""
    # Register (idempotent — 409 on repeat runs is fine)
    await client.post("/api/v1/auth/register", json=_TEST_USER)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": _TEST_USER["email"], "password": _TEST_USER["password"]},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
