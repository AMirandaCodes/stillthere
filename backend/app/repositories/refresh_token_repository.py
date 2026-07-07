from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(RefreshToken, session)

    async def create(
        self, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id, token_hash=token_hash, expires_at=expires_at
        )
        return await self.save(token)

    async def get_valid_by_hash(self, token_hash: str) -> RefreshToken | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def revoke(self, token_hash: str) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await self.session.flush()

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Return the token regardless of revocation/expiry status.

        Used by refresh() to detect when a previously-rotated token is reused —
        a potential theft indicator that triggers session-family revocation (AUTH-02).
        """
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        """Revoke every active token for a user — used for 'log out everywhere'."""
        await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await self.session.flush()
