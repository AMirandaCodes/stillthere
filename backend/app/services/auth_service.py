"""
AuthService — business logic for registration, login, token refresh, and logout.

Extracts all auth orchestration from the route layer so it can be tested
without HTTP. Raises ValueError with a code string on domain errors; routes
map these to the appropriate HTTPException.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    refresh_token_expires_at,
    verify_password,
)
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import TokenResponse


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)
        self._tokens = RefreshTokenRepository(session)

    async def register(self, email: str, full_name: str, password: str) -> User:
        """Create a new user. Raises ValueError('email_exists') if the address is taken."""
        if await self._users.email_exists(email):
            raise ValueError("email_exists")
        return await self._users.create(email, full_name, hash_password(password))

    async def login(self, email: str, password: str) -> TokenResponse:
        """
        Validate credentials and issue a token pair.

        Raises ValueError('invalid_credentials') for bad email/password,
        ValueError('inactive') for a suspended account.
        """
        user = await self._users.get_by_email(email)
        if not user or not verify_password(password, user.hashed_password):
            raise ValueError("invalid_credentials")
        if not user.is_active:
            raise ValueError("inactive")
        access = create_access_token(str(user.id))
        raw, hashed = generate_refresh_token()
        await self._tokens.create(user.id, hashed, refresh_token_expires_at())
        return TokenResponse(
            access_token=access,
            refresh_token=raw,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def refresh(self, raw_token: str) -> TokenResponse:
        """
        Rotate the refresh token and issue a new pair.
        Raises ValueError('invalid_token') if the token is missing, expired, or revoked.
        """
        stored = await self._tokens.get_valid_by_hash(hash_token(raw_token))
        if not stored:
            raise ValueError("invalid_token")
        await self._tokens.revoke(stored.token_hash)
        access = create_access_token(str(stored.user_id))
        raw, new_hash = generate_refresh_token()
        await self._tokens.create(stored.user_id, new_hash, refresh_token_expires_at())
        return TokenResponse(
            access_token=access,
            refresh_token=raw,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def logout(self, raw_token: str) -> None:
        """Revoke a refresh token. Silent if already invalid or expired."""
        await self._tokens.revoke(hash_token(raw_token))
