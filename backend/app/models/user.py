from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.search import Search
    from app.models.refresh_token import RefreshToken


class User(BaseModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(500), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # is_admin retained for future use — no route guards enforced at this stage
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set on password change to invalidate all tokens issued before that moment (AUTH-06).
    # NULL means no invalidation has been performed.
    token_issued_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    searches: Mapped[list["Search"]] = relationship("Search", back_populates="user")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"

    __table_args__ = (Index("ix_users_email", "email"),)
