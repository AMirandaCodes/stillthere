from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    echo=settings.DEBUG,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Celery tasks each call asyncio.run(), creating a new event loop per task.
# Pooled asyncpg connections are bound to the loop that created them, so
# reusing them across tasks raises "Future attached to a different loop".
# NullPool avoids this by never caching connections between calls.
_task_engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=settings.DEBUG,
    future=True,
)

TaskSessionLocal = async_sessionmaker(
    _task_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            if session.in_transaction():
                await session.commit()
        except Exception:
            await session.rollback()
            raise
