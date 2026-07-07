"""
Celery tasks for batch CSV processing.

Public surface:
  process_batch_job  — Celery task; dispatched by BatchService after upload
  process_batch_row  — Celery task; dispatched per PENDING JobResult

Internal:
  _process_batch_job_async  — async orchestrator called by process_batch_job
  _process_batch_row_async  — async orchestrator called by process_batch_row
  _increment_counters       — atomically updates BatchJob counters + checks completion

Design:
  BatchService pre-creates all DB records before dispatch:
    - PENDING  JobResult  → processed by process_batch_row
    - SKIPPED  JobResult  → already counted in processed_records at upload time

  process_batch_job:
    1. Idempotency: COMPLETE/FAILED → return immediately
    2. Set BatchJob.status=RUNNING
    3. Load all PENDING job_results, dispatch process_batch_row for each
    4. If zero pending rows (all skipped) → set COMPLETE immediately

  process_batch_row:
    1. Idempotency: status != PENDING → return
    2. Handle VerificationResult crash-recovery (RUNNING → delete partial evidence)
    3. Load pipeline inputs from DB
    4. Run run_pipeline() (same function used by single verifications)
    5. Write VerificationResult + JobResult via apply_pipeline_result
    6. Atomically increment BatchJob counters; set COMPLETE when done

  rate_limit="10/m" on process_batch_row throttles Serper and Anthropic API calls
  to ≤40 queries/minute when running a single batch worker.
"""
import asyncio
import traceback
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import TaskSessionLocal as AsyncSessionLocal
from app.models.batch_job import BatchJob
from app.models.enums import (
    BatchJobStatus,
    JobResultStatus,
    VerificationStatus,
)
from app.models.evidence_source import EvidenceSource
from app.models.job_result import JobResult
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.tasks.celery_app import celery_app
from app.tasks.pipeline import PipelineError, run_pipeline
from app.tasks.result_mapper import apply_pipeline_result

logger = get_logger(__name__)


class RowOutcome(StrEnum):
    SUCCESS = "success"
    UNCLEAR = "unclear"
    FAILED  = "failed"


def _user_error_message(exc: Exception) -> str:
    if isinstance(exc, PipelineError):
        return "Search failed. The service may be temporarily unavailable — please try again."
    return "An unexpected error occurred during verification."


# ── Row-level helpers ──────────────────────────────────────────────────────────

async def _clear_partial_evidence(
    session: AsyncSession, verification_result: VerificationResult
) -> None:
    """Crash recovery: delete any evidence written before the worker died."""
    await session.execute(
        delete(EvidenceSource).where(
            EvidenceSource.verification_result_id == verification_result.id
        )
    )
    logger.info(
        "Crash recovery: cleared partial evidence",
        verification_result_id=str(verification_result.id),
    )


async def _reconcile_already_complete(
    session: AsyncSession,
    job_result: JobResult,
    verification_result: VerificationResult,
    job_uuid: UUID,
) -> None:
    """
    Pipeline completed on a prior attempt but the JobResult update was lost.
    Reconcile by marking SUCCESS and incrementing counters now.
    """
    job_result.status = JobResultStatus.SUCCESS
    await session.commit()
    outcome = (
        RowOutcome.UNCLEAR
        if verification_result.confidence_score == 0
        else RowOutcome.SUCCESS
    )
    await _increment_counters(job_uuid, outcome)


# ── process_batch_job ──────────────────────────────────────────────────────────

async def _process_batch_job_async(batch_job_id: str) -> None:
    job_uuid = UUID(batch_job_id)

    # Session 1: Idempotency + RUNNING
    async with AsyncSessionLocal() as session:
        batch_job = await session.get(BatchJob, job_uuid)
        if batch_job is None:
            logger.error("BatchJob not found", batch_job_id=batch_job_id)
            return
        if batch_job.status in (BatchJobStatus.COMPLETE, BatchJobStatus.FAILED):
            logger.info(
                "BatchJob already terminal — skipping",
                batch_job_id=batch_job_id,
                status=batch_job.status,
            )
            return
        batch_job.status = BatchJobStatus.RUNNING
        batch_job.started_at = datetime.now(timezone.utc)
        await session.commit()

    # Session 2: Load PENDING job_result IDs (skip-on-restart: already-done rows excluded)
    async with AsyncSessionLocal() as session:
        stmt = select(JobResult.id).where(
            JobResult.batch_job_id == job_uuid,
            JobResult.status == JobResultStatus.PENDING,
        )
        pending_ids = list((await session.execute(stmt)).scalars().all())

    if not pending_ids:
        # All rows were skipped — mark complete immediately
        async with AsyncSessionLocal() as session:
            batch_job = await session.get(BatchJob, job_uuid)
            if batch_job and batch_job.status == BatchJobStatus.RUNNING:
                batch_job.status = BatchJobStatus.COMPLETE
                batch_job.completed_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info("Batch complete (all rows skipped)", batch_job_id=batch_job_id)
        return

    logger.info(
        "Dispatching batch row tasks",
        batch_job_id=batch_job_id,
        pending_count=len(pending_ids),
    )
    for job_result_id in pending_ids:
        process_batch_row.apply_async(
            args=[batch_job_id, str(job_result_id)],
            queue="batch",
        )


async def _mark_batch_job_failed_direct(batch_job_id: str) -> None:
    """Best-effort FAILED update for a BatchJob when Phase-1 DB failure occurs."""
    job_uuid = UUID(batch_job_id)
    async with AsyncSessionLocal() as session:
        batch_job = await session.get(BatchJob, job_uuid)
        if batch_job is not None and batch_job.status not in (
            BatchJobStatus.COMPLETE, BatchJobStatus.FAILED
        ):
            batch_job.status = BatchJobStatus.FAILED
            await session.commit()


@celery_app.task(
    bind=True,
    name="batch.process_job",
    acks_late=True,
)
def process_batch_job(self, batch_job_id: str) -> None:
    try:
        asyncio.run(_process_batch_job_async(batch_job_id))
    except Exception as exc:
        logger.error("Batch job task-level failure", batch_job_id=batch_job_id, error=str(exc))
        try:
            asyncio.run(_mark_batch_job_failed_direct(batch_job_id))
        except Exception:
            pass
        raise


# ── process_batch_row ──────────────────────────────────────────────────────────

async def _process_batch_row_async(batch_job_id: str, job_result_id: str) -> None:
    result_uuid = UUID(job_result_id)
    job_uuid = UUID(batch_job_id)

    # Session 1: Idempotency + crash-recovery for VerificationResult
    async with AsyncSessionLocal() as session:
        job_result = await session.get(JobResult, result_uuid)
        if job_result is None:
            logger.error("JobResult not found", job_result_id=job_result_id)
            return
        if job_result.status != JobResultStatus.PENDING:
            logger.info(
                "JobResult not PENDING — skipping",
                job_result_id=job_result_id,
                status=job_result.status,
            )
            return

        if job_result.verification_result_id is None:
            logger.error("JobResult has no linked VerificationResult", job_result_id=job_result_id)
            job_result.status = JobResultStatus.FAILED
            job_result.error_message = "Internal error: no verification result linked."
            await session.commit()
            await _increment_counters(job_uuid, RowOutcome.FAILED)
            return

        verification_result = await session.get(VerificationResult, job_result.verification_result_id)
        if verification_result is None:
            return

        if verification_result.status == VerificationStatus.COMPLETE:
            await _reconcile_already_complete(session, job_result, verification_result, job_uuid)
            return

        if verification_result.status == VerificationStatus.RUNNING:
            await _clear_partial_evidence(session, verification_result)
            logger.info("Crash recovery triggered", job_result_id=job_result_id)

        verification_result.status = VerificationStatus.RUNNING
        await session.commit()

    # Session 2: Load pipeline inputs (read-only)
    async with AsyncSessionLocal() as session:
        stmt = (
            select(JobResult)
            .options(
                selectinload(JobResult.verification_result).options(
                    selectinload(VerificationResult.search).options(
                        selectinload(Search.contact),
                        selectinload(Search.company),
                    )
                )
            )
            .where(JobResult.id == result_uuid)
        )
        job_result = (await session.execute(stmt)).scalar_one_or_none()
        if job_result is None:
            return
        name = job_result.verification_result.search.contact.full_name
        company = job_result.verification_result.search.company.name
        email = job_result.verification_result.search.submitted_email
        ver_id = job_result.verification_result_id

    # Run pipeline (no DB)
    pipeline_result = None
    error_msg: str | None = None

    try:
        pipeline_result = await run_pipeline(name, company, email)
    except Exception as exc:
        error_msg = _user_error_message(exc)
        logger.error(
            "Batch row pipeline failed",
            job_result_id=job_result_id,
            exc_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
        )

    # Session 3: Write results + atomically increment counters.
    # Counter increment is merged into this transaction (BL-01) so a worker
    # crash cannot leave processed_records permanently short of total_records.
    if pipeline_result is None:
        outcome = RowOutcome.FAILED
    elif pipeline_result.confidence_score == 0:
        outcome = RowOutcome.UNCLEAR
    else:
        outcome = RowOutcome.SUCCESS

    async with AsyncSessionLocal() as session:
        job_result = await session.get(JobResult, result_uuid)
        verification_result = await session.get(VerificationResult, ver_id)
        if job_result is None or verification_result is None:
            return

        if outcome == RowOutcome.FAILED:
            verification_result.status = VerificationStatus.FAILED
            verification_result.error_message = error_msg
            job_result.status = JobResultStatus.FAILED
            job_result.error_message = error_msg
        else:
            apply_pipeline_result(verification_result, pipeline_result, session, ver_id)
            job_result.status = JobResultStatus.SUCCESS

        counter_result = await session.execute(
            update(BatchJob)
            .where(BatchJob.id == job_uuid)
            .values(
                processed_records=BatchJob.processed_records + 1,
                failed_records=BatchJob.failed_records + (
                    1 if outcome == RowOutcome.FAILED else 0
                ),
                unclear_records=BatchJob.unclear_records + (
                    1 if outcome == RowOutcome.UNCLEAR else 0
                ),
                successful_records=BatchJob.successful_records + (
                    1 if outcome == RowOutcome.SUCCESS else 0
                ),
            )
            .returning(
                BatchJob.processed_records,
                BatchJob.total_records,
                BatchJob.status,
            )
        )
        counter_row = counter_result.one_or_none()
        if (
            counter_row is not None
            and counter_row.processed_records >= counter_row.total_records
            and counter_row.status == BatchJobStatus.RUNNING
        ):
            await session.execute(
                update(BatchJob)
                .where(
                    BatchJob.id == job_uuid,
                    BatchJob.status == BatchJobStatus.RUNNING,
                )
                .values(
                    status=BatchJobStatus.COMPLETE,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            logger.info("Batch job complete", batch_job_id=str(job_uuid))
        await session.commit()


async def _mark_batch_row_failed_direct(
    batch_job_id: str, job_result_id: str, error_message: str
) -> None:
    """Best-effort FAILED update for a batch row when Phase-1 DB failure occurs."""
    result_uuid = UUID(job_result_id)
    job_uuid = UUID(batch_job_id)
    async with AsyncSessionLocal() as session:
        job_result = await session.get(JobResult, result_uuid)
        if job_result is not None and job_result.status == JobResultStatus.PENDING:
            job_result.status = JobResultStatus.FAILED
            job_result.error_message = error_message
            if job_result.verification_result_id:
                verification_result = await session.get(
                    VerificationResult, job_result.verification_result_id
                )
                if verification_result is not None and verification_result.status not in (
                    VerificationStatus.COMPLETE, VerificationStatus.FAILED
                ):
                    verification_result.status = VerificationStatus.FAILED
                    verification_result.error_message = error_message
            await session.commit()
    await _increment_counters(job_uuid, RowOutcome.FAILED)


@celery_app.task(
    bind=True,
    name="batch.process_row",
    acks_late=True,
    rate_limit="10/m",
)
def process_batch_row(self, batch_job_id: str, job_result_id: str) -> None:
    try:
        asyncio.run(_process_batch_row_async(batch_job_id, job_result_id))
    except Exception as exc:
        logger.error(
            "Batch row task-level failure",
            batch_job_id=batch_job_id,
            job_result_id=job_result_id,
            error=str(exc),
        )
        try:
            asyncio.run(
                _mark_batch_row_failed_direct(
                    batch_job_id, job_result_id, "Verification could not be processed."
                )
            )
        except Exception:
            pass
        raise


# ── Counter helper ─────────────────────────────────────────────────────────────

async def _increment_counters(job_uuid: UUID, outcome: RowOutcome) -> None:
    """
    Atomically increment BatchJob counters and mark COMPLETE when all rows done.

    Runs in its own session after the write session commits so that the
    RETURNING values reflect the fully-committed counter state.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(BatchJob)
            .where(BatchJob.id == job_uuid)
            .values(
                processed_records=BatchJob.processed_records + 1,
                failed_records=BatchJob.failed_records + (
                    1 if outcome == RowOutcome.FAILED else 0
                ),
                unclear_records=BatchJob.unclear_records + (
                    1 if outcome == RowOutcome.UNCLEAR else 0
                ),
                successful_records=BatchJob.successful_records + (
                    1 if outcome == RowOutcome.SUCCESS else 0
                ),
            )
            .returning(
                BatchJob.processed_records,
                BatchJob.total_records,
                BatchJob.status,
            )
        )
        row = result.one_or_none()
        if (
            row is not None
            and row.processed_records >= row.total_records
            and row.status == BatchJobStatus.RUNNING
        ):
            await session.execute(
                update(BatchJob)
                .where(
                    BatchJob.id == job_uuid,
                    BatchJob.status == BatchJobStatus.RUNNING,
                )
                .values(
                    status=BatchJobStatus.COMPLETE,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            logger.info("Batch job complete", batch_job_id=str(job_uuid))
        await session.commit()
