from typing import TYPE_CHECKING

from sqlalchemy import String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.utils import normalise_name
from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.search import Search


class Company(BaseModel):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    searches: Mapped[list["Search"]] = relationship("Search", back_populates="company")

    @validates("name")
    def _normalise_name(self, _key: str, value: str) -> str:
        self.normalized_name = normalise_name(value)
        return value

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r}>"

    __table_args__ = (
        Index("ix_companies_normalized_name", "normalized_name"),
        Index("ix_companies_domain", "domain"),
    )
