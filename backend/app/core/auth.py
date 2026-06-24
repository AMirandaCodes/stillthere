"""
Authentication utilities: password hashing, JWT access tokens, opaque refresh tokens.

Refresh token design: a cryptographically random 128-character hex string is generated
and returned to the client; only its SHA-256 hash is stored in the database.  If the
token table is ever compromised, the raw tokens cannot be derived from the hashes.
Token rotation is enforced on every /refresh call — the old token is revoked and a
new pair is issued.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Expose as module-level constants so routes can reference them without re-importing settings
ACCESS_TOKEN_EXPIRE_MINUTES: int = get_settings().ACCESS_TOKEN_EXPIRE_MINUTES


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(subject: str) -> str:
    """Issue a signed JWT valid for ACCESS_TOKEN_EXPIRE_MINUTES minutes."""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.
    Raises jose.JWTError if the token is invalid, expired, or the wrong type.
    """
    settings = get_settings()
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise JWTError("Wrong token type")
    return payload


def generate_refresh_token() -> tuple[str, str]:
    """
    Generate a new opaque refresh token.
    Returns (raw_token_for_client, sha256_hash_for_db).
    """
    raw = secrets.token_hex(64)  # 128-char hex string
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_token(raw: str) -> str:
    """Re-derive the hash from a raw token for DB lookups."""
    return hashlib.sha256(raw.encode()).hexdigest()


def refresh_token_expires_at() -> datetime:
    settings = get_settings()
    return datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
