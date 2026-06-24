"""
Search model — represents a single user-submitted verification request.

Separated from VerificationResult intentionally:
  - A Search is created immediately when the user submits a request.
  - A VerificationResult is created when the background job starts processing.
  - One Search can produce multiple VerificationResults over time (re-verification).
  - Keeping them apart provides a clean audit trail of what was requested vs what was found.
"""
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, ForeignKey, Enum as SAEnum, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.enums import SearchSource

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.company import Company
    from app.models.batch_job import BatchJob
    from app.models.user import User
    from app.models.verification_result import VerificationResult
    from app.models.job_result import JobResult


class Search(BaseModel):
    __tablename__ = "searches"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    submitted_email: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[SearchSource] = mapped_column(
        SAEnum(SearchSource, native_enum=False, length=20, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=SearchSource.SINGLE,
    )
    batch_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("batch_jobs.id", ondelete="SET NULL"), nullable=True
    )
    # Nullable audit column: records who submitted the search; NULL for batch rows
    # and unauthenticated dev calls.  No data-separation logic is enforced on it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    contact: Mapped["Contact"] = relationship("Contact", back_populates="searches")
    company: Mapped["Company"] = relationship("Company", back_populates="searches")
    batch_job: Mapped["BatchJob | None"] = relationship("BatchJob", back_populates="searches")
    user: Mapped["User | None"] = relationship("User", back_populates="searches")
    verification_results: Mapped[list["VerificationResult"]] = relationship(
        "VerificationResult", back_populates="search", order_by="VerificationResult.created_at.desc()"
    )
    job_result: Mapped["JobResult | None"] = relationship("JobResult", back_populates="search", uselist=False)

    @property
    def latest_result(self) -> "VerificationResult | None":
        return self.verification_results[0] if self.verification_results else None

    def __repr__(self) -> str:
        return f"<Search id={self.id} contact_id={self.contact_id} source={self.source}>"

    __table_args__ = (
        Index("ix_searches_contact_id", "contact_id"),
        Index("ix_searches_company_id", "company_id"),
        Index("ix_searches_batch_job_id", "batch_job_id"),
        Index("ix_searches_user_id", "user_id"),
        Index("ix_searches_created_at", "created_at"),
    )
