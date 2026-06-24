import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Integer, Text, ForeignKey, Enum as SAEnum, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.enums import JobResultStatus

if TYPE_CHECKING:
    from app.models.batch_job import BatchJob
    from app.models.search import Search
    from app.models.verification_result import VerificationResult


class JobResult(BaseModel):
    __tablename__ = "job_results"

    batch_job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("batch_jobs.id", ondelete="CASCADE"), nullable=False
    )
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("searches.id", ondelete="SET NULL"), nullable=True
    )
    verification_result_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("verification_results.id", ondelete="SET NULL"),
        nullable=True,
    )

    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[JobResultStatus] = mapped_column(
        SAEnum(JobResultStatus, native_enum=False, length=20, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=JobResultStatus.SUCCESS,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Preserves the original CSV row so failed rows can be inspected and retried
    raw_csv_row: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    # Relationships
    batch_job: Mapped["BatchJob"] = relationship("BatchJob", back_populates="job_results")
    search: Mapped["Search | None"] = relationship("Search", back_populates="job_result")
    verification_result: Mapped["VerificationResult | None"] = relationship(
        "VerificationResult", back_populates="job_result"
    )

    def __repr__(self) -> str:
        return f"<JobResult id={self.id} row={self.row_number} status={self.status}>"

    __table_args__ = (
        Index("ix_job_results_batch_job_id", "batch_job_id"),
        Index("ix_job_results_search_id", "search_id"),
        Index("ix_job_results_status", "status"),
    )
