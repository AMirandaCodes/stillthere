"""
AuthService — business logic for registration, login, token refresh, and logout.

Extracts all auth orchestration from the route layer so it can be tested
without HTTP. Raises AuthError with a code string on domain errors; routes
map these to the appropriate HTTPException.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    dummy_verify,
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


class AuthError(Exception):
    """Domain error from AuthService. Routes map .code to an HTTPException."""
    EMAIL_EXISTS        = "email_exists"
    INVALID_CREDENTIALS = "invalid_credentials"
    INACTIVE            = "inactive"
    INVALID_TOKEN       = "invalid_token"

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        user_repo: UserRepository | None = None,
        token_repo: RefreshTokenRepository | None = None,
    ) -> None:
        self._users = user_repo or UserRepository(session)
        self._tokens = token_repo or RefreshTokenRepository(session)

    async def register(self, email: str, full_name: str, password: str) -> User:
        """Create a new user. Raises AuthError.EMAIL_EXISTS if the address is taken."""
        if await self._users.email_exists(email):
            raise AuthError(AuthError.EMAIL_EXISTS)
        return await self._users.create(email, full_name, hash_password(password))

    async def login(self, email: str, password: str) -> TokenResponse:
        """
        Validate credentials and issue a token pair.

        Always calls a bcrypt comparison regardless of whether the user exists
        so that the response time is constant — prevents timing-based account
        enumeration via the login endpoint (CWE-203, AUTH-01).
        """
        user = await self._users.get_by_email(email)
        if not user:
            dummy_verify(password)  # constant-time guard — discard result
            raise AuthError(AuthError.INVALID_CREDENTIALS)
        if not verify_password(password, user.hashed_password):
            raise AuthError(AuthError.INVALID_CREDENTIALS)
        if not user.is_active:
            raise AuthError(AuthError.INACTIVE)
        access = create_access_token(str(user.id))
        raw_token, token_hash = generate_refresh_token()
        await self._tokens.create(user.id, token_hash, refresh_token_expires_at())
        return TokenResponse(
            access_token=access,
            refresh_token=raw_token,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def refresh(self, raw_token: str) -> TokenResponse:
        """
        Rotate the refresh token and issue a new pair.

        If the token exists in the DB but was already revoked, we treat it as a
        potential theft scenario and revoke ALL active sessions for that user
        (session-family revocation, AUTH-02). Raises AuthError('invalid_token')
        if the token is missing, expired, or revoked.
        """
        token_hash = hash_token(raw_token)
        stored = await self._tokens.get_valid_by_hash(token_hash)
        if not stored:
            # Token not valid — check if it exists at all (already rotated = possible theft)
            revoked = await self._tokens.get_by_hash(token_hash)
            if revoked:
                await self._tokens.revoke_all_for_user(revoked.user_id)
            raise AuthError(AuthError.INVALID_TOKEN)
        await self._tokens.revoke(stored.token_hash)
        access = create_access_token(str(stored.user_id))
        raw_token, token_hash = generate_refresh_token()
        await self._tokens.create(stored.user_id, token_hash, refresh_token_expires_at())
        return TokenResponse(
            access_token=access,
            refresh_token=raw_token,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def logout(self, raw_token: str) -> None:
        """Revoke a refresh token. Silent if already invalid or expired."""
        await self._tokens.revoke(hash_token(raw_token))
