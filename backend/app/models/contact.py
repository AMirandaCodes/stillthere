import re
from typing import TYPE_CHECKING

from sqlalchemy import String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.search import Search


class Contact(BaseModel):
    __tablename__ = "contacts"

    full_name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(500), nullable=True, unique=True, index=True)

    # Relationships
    searches: Mapped[list["Search"]] = relationship("Search", back_populates="contact")

    @validates("full_name")
    def _normalise_name(self, _key: str, value: str) -> str:
        normalised = re.sub(r"\s+", " ", value.strip().lower())
        self.normalized_name = normalised
        return value

    def __repr__(self) -> str:
        return f"<Contact id={self.id} name={self.full_name!r}>"

    __table_args__ = (
        Index("ix_contacts_normalized_name", "normalized_name"),
    )
