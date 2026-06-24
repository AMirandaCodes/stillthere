from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.email == email.lower().strip())
        )
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        result = await self.session.execute(
            select(User.id).where(User.email == email.lower().strip())
        )
        return result.scalar_one_or_none() is not None

    async def create(self, email: str, full_name: str, hashed_password: str) -> User:
        user = User(
            email=email.lower().strip(),
            full_name=full_name.strip(),
            hashed_password=hashed_password,
        )
        return await self.save(user)

    async def set_active(self, user_id: UUID, active: bool) -> User | None:
        user = await self.get_by_id(user_id)
        if user:
            user.is_active = active
            await self.session.flush()
        return user
