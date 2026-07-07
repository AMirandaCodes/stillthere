"""
Shared pytest fixtures.

Fixture hierarchy:
  event_loop   (function-scoped) → fresh asyncio loop per test with task cleanup
  test_engine  (session-scoped)  → creates all tables once per test run
  db_session   (function-scoped) → fresh session per test; TRUNCATE on teardown
  client       (function-scoped) → httpx AsyncClient wired to the test DB
  auth_headers (function-scoped) → registers a test user, returns Bearer headers

NullPool on test_engine
------------------------
asyncpg connections are bound to the event loop that created them.
With NullPool, every session checkout creates a fresh asyncpg connection on the
CURRENT running loop, so function-scoped sessions are always on the function
loop even though test_engine itself is session-scoped. Without NullPool, pooled
connections from the session-setup loop would be reused on function loops,
causing "Future attached to a different loop" errors.

Function-scoped event_loop with task cleanup
---------------------------------------------
Starlette's BaseHTTPMiddleware (used by SlowAPIMiddleware) creates an asyncio
Task for each request's call_next coroutine that stays alive until the ASGI
app receives http.disconnect. With a session-scoped loop, these tasks
accumulate across tests and eventually collide. Using a function-scoped loop
and explicitly cancelling pending tasks on teardown ensures a clean slate for
every test.

asyncio.set_event_loop(loop) in the fixture ensures asyncio.get_event_loop()
returns the right loop both inside coroutines (get_running_loop) and in
synchronous code called from within tests (asyncpg, anyio, slowapi).

Rate limiting
-------------
RATE_LIMITS_ENABLED=false must be set before any app import so the
module-level Limiter singleton reads it at construction time.
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


@pytest.fixture
def event_loop():
    """Per-test asyncio event loop with explicit task cleanup.

    Creates a fresh loop for every test and tears it down cleanly:
    1. asyncio.set_event_loop(loop) — ensures asyncio.get_event_loop() returns
       this loop even from synchronous code (asyncpg connect, anyio backend,
       SlowAPIMiddleware) so all Futures are created on the right loop.
    2. After yield, cancels any tasks still pending (mainly Starlette's
       BaseHTTPMiddleware call_next task waiting for http.disconnect) and drains
       them before closing the loop.
    3. asyncio.set_event_loop(None) clears the thread-local loop reference so
       the next test starts from a clean state.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop

    # Cancel tasks left pending by Starlette's BaseHTTPMiddleware (SlowAPIMiddleware).
    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

    asyncio.set_event_loop(None)
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def _create_tables():
    """Session-scoped schema setup: drop_all → create_all once per run, drop on exit.

    Runs on pytest-asyncio's internal session loop. Holds no persistent
    connections after the setup/teardown coroutines complete (NullPool).
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_engine(_create_tables):
    """Function-scoped engine: a fresh AsyncEngine per test, always on the current loop.

    Because this fixture is function-scoped it runs on L_func (the per-test
    loop provided by the event_loop fixture). All asyncpg connections created
    from this engine — whether by db_session or by _session_factory_patch in
    task tests — are therefore registered with L_func's selector. This
    eliminates the cross-loop asyncpg hang that arises when a session-scoped
    engine created on a different internal loop is reused on function loops.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    yield engine
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


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """
    Reset module-level circuit breaker singletons between tests.

    Without this, a test that trips a breaker leaves it OPEN for the next test,
    causing false failures when the breaker is fast-failing calls that would
    otherwise succeed.
    """
    from app.core.circuit_breakers import anthropic_breaker, serper_breaker

    def _reset(b):
        b._failures = 0
        b._opened_at = 0.0
        b._open = False

    _reset(serper_breaker)
    _reset(anthropic_breaker)
    yield
    _reset(serper_breaker)
    _reset(anthropic_breaker)
