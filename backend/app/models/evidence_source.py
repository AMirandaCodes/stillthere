import uuid
from datetime import datetime

from sqlalchemy import String, Text, ForeignKey, DateTime, Enum as SAEnum, func, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

from app.models.base import BaseModel
from app.models.enums import EvidenceSourceType

if TYPE_CHECKING:
    from app.models.verification_result import VerificationResult


class EvidenceSource(BaseModel):
    __tablename__ = "evidence_sources"

    verification_result_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("verification_results.id", ondelete="CASCADE"),
        nullable=False,
    )

    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    # explanation records *why* this source supports the conclusion, written by the AI analyser
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[EvidenceSourceType] = mapped_column(
        SAEnum(EvidenceSourceType, native_enum=False, length=50, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=EvidenceSourceType.SEARCH_RESULT,
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    verification_result: Mapped["VerificationResult"] = relationship(
        "VerificationResult", back_populates="evidence_sources"
    )

    def __repr__(self) -> str:
        return f"<EvidenceSource id={self.id} type={self.source_type} url={self.url[:60]!r}>"

    __table_args__ = (
        Index("ix_evidence_sources_verification_result_id", "verification_result_id"),
    )
