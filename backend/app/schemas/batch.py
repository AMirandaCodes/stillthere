from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import BatchJobStatus, JobResultStatus
from app.schemas.verification import VerificationSummary


class BatchJobResponse(BaseModel):
    id: UUID
    filename: str
    status: BatchJobStatus
    total_records: int
    processed_records: int
    successful_records: int
    failed_records: int
    unclear_records: int
    progress_percentage: int
    celery_task_id: str | None = None
    uploaded_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class JobResultResponse(BaseModel):
    """Per-row result returned by GET /{job_id}/results."""
    id: UUID
    row_number: int
    status: JobResultStatus
    error_message: str | None
    raw_csv_row: dict
    verification: VerificationSummary | None
