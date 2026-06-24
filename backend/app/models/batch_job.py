from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, Enum as SAEnum, DateTime, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.enums import BatchJobStatus

if TYPE_CHECKING:
    from app.models.job_result import JobResult
    from app.models.search import Search


class BatchJob(BaseModel):
    __tablename__ = "batch_jobs"

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[BatchJobStatus] = mapped_column(
        SAEnum(BatchJobStatus, native_enum=False, length=20, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=BatchJobStatus.QUEUED,
        index=True,
    )
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unclear_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    job_results: Mapped[list["JobResult"]] = relationship("JobResult", back_populates="batch_job")
    searches: Mapped[list["Search"]] = relationship("Search", back_populates="batch_job")

    @property
    def progress_percentage(self) -> int:
        if self.total_records == 0:
            return 0
        return round((self.processed_records / self.total_records) * 100)

    def __repr__(self) -> str:
        return f"<BatchJob id={self.id} filename={self.filename!r} status={self.status}>"

    __table_args__ = (
        Index("ix_batch_jobs_status", "status"),
        Index("ix_batch_jobs_created_at", "created_at"),
    )
