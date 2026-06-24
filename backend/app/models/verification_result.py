import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, ForeignKey, Text, Enum as SAEnum, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import SmallInteger

from app.models.base import BaseModel
from app.models.enums import VerificationStatus, TriState, ConfidenceLevel

if TYPE_CHECKING:
    from app.models.search import Search
    from app.models.evidence_source import EvidenceSource
    from app.models.job_result import JobResult


class VerificationResult(BaseModel):
    __tablename__ = "verification_results"

    search_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("searches.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[VerificationStatus] = mapped_column(
        SAEnum(VerificationStatus, native_enum=False, length=20, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=VerificationStatus.PENDING,
        index=True,
    )

    # --- Report fields (all default to UNCLEAR until evidence is found) -------
    person_found: Mapped[TriState] = mapped_column(
        SAEnum(TriState, native_enum=False, length=10, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TriState.UNCLEAR,
    )
    appears_associated: Mapped[TriState] = mapped_column(
        SAEnum(TriState, native_enum=False, length=10, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TriState.UNCLEAR,
    )
    found_on_website: Mapped[TriState] = mapped_column(
        SAEnum(TriState, native_enum=False, length=10, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TriState.UNCLEAR,
    )
    company_active: Mapped[TriState] = mapped_column(
        SAEnum(TriState, native_enum=False, length=10, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TriState.UNCLEAR,
    )
    email_match: Mapped[TriState] = mapped_column(
        SAEnum(TriState, native_enum=False, length=10, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TriState.UNCLEAR,
    )

    # --- Confidence -----------------------------------------------------------
    confidence_score: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    confidence_level: Mapped[ConfidenceLevel] = mapped_column(
        SAEnum(ConfidenceLevel, native_enum=False, length=10, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ConfidenceLevel.LOW,
    )

    # --- Supporting data ------------------------------------------------------
    useful_links: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Raw Serper + fetched page data retained for debugging and re-analysis
    raw_search_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- Job tracking ---------------------------------------------------------
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    search: Mapped["Search"] = relationship("Search", back_populates="verification_results")
    evidence_sources: Mapped[list["EvidenceSource"]] = relationship(
        "EvidenceSource",
        back_populates="verification_result",
        cascade="all, delete-orphan",
        order_by="EvidenceSource.collected_at",
    )
    job_result: Mapped["JobResult | None"] = relationship(
        "JobResult", back_populates="verification_result", uselist=False
    )

    def __repr__(self) -> str:
        return (
            f"<VerificationResult id={self.id} status={self.status} "
            f"confidence={self.confidence_score}>"
        )

    __table_args__ = (
        Index("ix_verification_results_search_id", "search_id"),
        Index("ix_verification_results_status", "status"),
        Index("ix_verification_results_confidence_score", "confidence_score"),
        Index("ix_verification_results_created_at", "created_at"),
    )
