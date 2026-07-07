import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator

from app.core.security import sanitise_name

_COMMON_PASSWORDS = {
    "password", "password1", "password123",
    "12345678", "123456789", "1234567890",
    "qwerty123", "qwertyui", "iloveyou",
    "letmein1", "welcome1", "monkey123",
}


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = sanitise_name(v)
        if not v:
            raise ValueError("full_name must not be empty")
        return v

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if v.lower() in _COMMON_PASSWORDS:
            raise ValueError("Password is too common — choose a more unique password")
        if not re.search(r"[A-Za-z]", v):
            raise ValueError("Password must contain at least one letter")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one number")
        return v


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    is_active: bool
    is_admin: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expiry


class RefreshRequest(BaseModel):
    refresh_token: str
