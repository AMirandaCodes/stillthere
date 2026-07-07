from typing import TYPE_CHECKING

from sqlalchemy import String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.utils import normalize_name
from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.search import Search


class Contact(BaseModel):
    __tablename__ = "contacts"

    full_name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    email: Mapped[str | None] = mapped_column(String(500), nullable=True, unique=True, index=True)

    # Relationships
    searches: Mapped[list["Search"]] = relationship("Search", back_populates="contact")

    @validates("full_name")
    def _normalize_name(self, _key: str, value: str) -> str:
        self.normalized_name = normalize_name(value)
        return value

    def __repr__(self) -> str:
        return f"<Contact id={self.id} name={self.full_name!r}>"

    __table_args__ = (
        Index("ix_contacts_normalized_name", "normalized_name"),
    )
