"""
Generic repository base class.
Concrete repositories extend this and add domain-specific query methods.
"""
from typing import Awaitable, Callable, Generic, Type, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class BaseRepository(Generic[ModelT]):
    def __init__(self, model: Type[ModelT], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get_by_id(self, id: UUID) -> ModelT | None:
        result = await self.session.execute(select(self.model).where(self.model.id == id))
        return result.scalar_one_or_none()

    async def get_all(self, offset: int = 0, limit: int = 20, order_by=None) -> list[ModelT]:
        stmt = select(self.model)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return list(result.scalars().all())

    async def save(self, instance: ModelT) -> ModelT:
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def delete(self, instance: ModelT) -> None:
        await self.session.delete(instance)
        await self.session.flush()

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(self.model.id)))
        return result.scalar_one()

    async def _get_or_create(
        self,
        fetch: Callable[[], Awaitable[ModelT | None]],
        build: Callable[[], ModelT],
    ) -> tuple[ModelT, bool]:
        """
        Fetch an existing record or create a new one, handling concurrent
        creation races via IntegrityError retry.
        Returns (instance, was_created).
        """
        existing = await fetch()
        if existing:
            return existing, False
        try:
            return await self.save(build()), True
        except IntegrityError:
            await self.session.rollback()
            existing = await fetch()
            return existing, False  # type: ignore[return-value]
