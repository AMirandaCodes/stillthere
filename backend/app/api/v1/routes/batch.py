"""
Batch CSV processing endpoints.

Routes (all under /api/v1/batch via main.py prefix):
  POST  /upload           Upload CSV → BatchJobResponse (202 ACCEPTED)
  GET   /                 Paginated list of all batch jobs
  GET   /{job_id}         Single job status (poll until status=complete/failed)
  GET   /{job_id}/results Paginated per-row results
  GET   /{job_id}/export  Stream results as CSV download (job must be complete)
"""
import math
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession, PaginationDep
from app.models.enums import BatchJobStatus
from app.schemas.batch import BatchJobResponse, JobResultResponse
from app.schemas.common import PaginatedResponse
from app.services.batch_service import BatchService, BatchValidationError

router = APIRouter()


@router.post(
    "/upload",
    response_model=BatchJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a CSV for batch verification",
    description=(
        "Accepts a CSV file with Name, Company, and optional Email columns. "
        "Creates one verification job per valid row. "
        f"Rows with missing Name or Company are skipped. "
        "Returns immediately — poll GET /{job_id} for progress."
    ),
)
async def upload_batch(
    db: DbSession,
    current_user: CurrentUser,
    file: UploadFile = File(description="CSV file. Required columns: Name, Company. Optional: Email."),
) -> BatchJobResponse:
    service = BatchService(db)
    try:
        return await service.upload(file, user_id=current_user.id)
    except BatchValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get(
    "",
    response_model=PaginatedResponse[BatchJobResponse],
    summary="List all batch jobs",
)
async def list_batch_jobs(
    pagination: PaginationDep,
    db: DbSession,
    current_user: CurrentUser,
) -> PaginatedResponse[BatchJobResponse]:
    service = BatchService(db)
    return await service.list_jobs(
        offset=pagination.offset, limit=pagination.page_size, user_id=current_user.id
    )


@router.get(
    "/{job_id}",
    response_model=BatchJobResponse,
    summary="Get batch job status",
    description=(
        "Returns the current status and progress of a batch job. "
        "Poll this endpoint until status is 'complete' or 'failed'."
    ),
)
async def get_batch_job(
    job_id: UUID,
    db: DbSession,
    _: CurrentUser,
) -> BatchJobResponse:
    service = BatchService(db)
    result = await service.get_job(job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch job {job_id} not found.",
        )
    return result


@router.get(
    "/{job_id}/results",
    response_model=PaginatedResponse[JobResultResponse],
    summary="Get per-row results for a batch job",
)
async def get_batch_job_results(
    job_id: UUID,
    pagination: PaginationDep,
    db: DbSession,
    _: CurrentUser,
) -> PaginatedResponse[JobResultResponse]:
    service = BatchService(db)
    batch_job = await service.get_job(job_id)
    if batch_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch job {job_id} not found.",
        )
    return await service.get_job_results(
        job_id=job_id,
        offset=pagination.offset,
        limit=pagination.page_size,
    )


@router.get(
    "/{job_id}/export",
    summary="Download batch results as CSV",
    description=(
        "Streams the full result set as a downloadable CSV. "
        "Only available once the job status is 'complete'."
    ),
    responses={
        200: {"content": {"text/csv": {}}, "description": "CSV file"},
        400: {"description": "Job is not yet complete"},
        404: {"description": "Job not found"},
    },
)
async def export_batch_results(
    job_id: UUID,
    db: DbSession,
    _: CurrentUser,
) -> StreamingResponse:
    service = BatchService(db)
    batch_job = await service.get_job(job_id)
    if batch_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch job {job_id} not found.",
        )
    if batch_job.status != BatchJobStatus.COMPLETE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Batch job is not yet complete (status: {batch_job.status.value}). "
                "Export is only available after all rows have been processed."
            ),
        )
    return StreamingResponse(
        BatchService.export_csv_stream(job_id),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="batch_{job_id}_results.csv"'
        },
    )
