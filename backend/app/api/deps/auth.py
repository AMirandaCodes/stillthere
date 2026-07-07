from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract and validate the Bearer JWT, then load the user from the database.
    Raises HTTP 401 if the token is missing, invalid, or expired.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await UserRepository(db).get_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Reject tokens issued before a credential-change invalidation timestamp (AUTH-06).
    if user.token_issued_before is not None:
        iat = payload.get("iat")
        if iat is not None and iat < user.token_issued_before.timestamp():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session invalidated — please log in again",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Like get_current_user but additionally requires is_admin=True."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Like get_current_user but returns None for missing/invalid tokens instead of 401."""
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
    except JWTError:
        return None
    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        return None
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        return None
    user = await UserRepository(db).get_by_id(user_id)
    if not user or not user.is_active:
        return None
    if user.token_issued_before is not None:
        iat = payload.get("iat")
        if iat is not None and iat < user.token_issued_before.timestamp():
            return None
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]
