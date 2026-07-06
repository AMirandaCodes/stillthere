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
    5. Write VerificationResult + JobResult via _apply_pipeline_result
    6. Atomically increment BatchJob counters; set COMPLETE when done

  rate_limit="10/m" on process_batch_row throttles Serper and Anthropic API calls
  to ≤40 queries/minute when running a single batch worker.
"""
import asyncio
import traceback
from datetime import datetime, timezone
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
from app.tasks.pipeline import _PipelineError, _apply_pipeline_result, run_pipeline

logger = get_logger(__name__)


def _user_error_message(exc: Exception) -> str:
    if isinstance(exc, _PipelineError):
        return "Search failed. The service may be temporarily unavailable — please try again."
    return "An unexpected error occurred during verification."


# ── Row-level helpers ──────────────────────────────────────────────────────────

async def _clear_partial_evidence(session: AsyncSession, ver: VerificationResult) -> None:
    """Crash recovery: delete any evidence written before the worker died."""
    await session.execute(
        delete(EvidenceSource).where(EvidenceSource.verification_result_id == ver.id)
    )
    logger.info(
        "Crash recovery: cleared partial evidence",
        verification_result_id=str(ver.id),
    )


async def _reconcile_already_complete(
    session: AsyncSession,
    jr: JobResult,
    ver: VerificationResult,
    job_uuid: UUID,
) -> None:
    """
    Pipeline completed on a prior attempt but the JobResult update was lost.
    Reconcile by marking SUCCESS and incrementing counters now.
    """
    jr.status = JobResultStatus.SUCCESS
    await session.commit()
    await _increment_counters(job_uuid, failed=False, unclear=(ver.confidence_score == 0))


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
    for jr_id in pending_ids:
        process_batch_row.apply_async(
            args=[batch_job_id, str(jr_id)],
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
        jr = await session.get(JobResult, result_uuid)
        if jr is None:
            logger.error("JobResult not found", job_result_id=job_result_id)
            return
        if jr.status != JobResultStatus.PENDING:
            logger.info(
                "JobResult not PENDING — skipping",
                job_result_id=job_result_id,
                status=jr.status,
            )
            return

        if jr.verification_result_id is None:
            logger.error("JobResult has no linked VerificationResult", job_result_id=job_result_id)
            jr.status = JobResultStatus.FAILED
            jr.error_message = "Internal error: no verification result linked."
            await session.commit()
            await _increment_counters(job_uuid, failed=True)
            return

        ver = await session.get(VerificationResult, jr.verification_result_id)
        if ver is None:
            return

        if ver.status == VerificationStatus.COMPLETE:
            await _reconcile_already_complete(session, jr, ver, job_uuid)
            return

        if ver.status == VerificationStatus.RUNNING:
            await _clear_partial_evidence(session, ver)
            logger.info("Crash recovery triggered", job_result_id=job_result_id)

        ver.status = VerificationStatus.RUNNING
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
        jr_loaded = (await session.execute(stmt)).scalar_one_or_none()
        if jr_loaded is None:
            return
        name = jr_loaded.verification_result.search.contact.full_name
        company = jr_loaded.verification_result.search.company.name
        email = jr_loaded.verification_result.search.submitted_email
        ver_id = jr_loaded.verification_result_id

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

    # Session 3: Write results
    failed = pipeline_result is None
    unclear = False

    async with AsyncSessionLocal() as session:
        jr = await session.get(JobResult, result_uuid)
        ver = await session.get(VerificationResult, ver_id)
        if jr is None or ver is None:
            return

        if failed:
            ver.status = VerificationStatus.FAILED
            ver.error_message = error_msg
            jr.status = JobResultStatus.FAILED
            jr.error_message = error_msg
        else:
            _apply_pipeline_result(ver, pipeline_result, session, ver_id)
            jr.status = JobResultStatus.SUCCESS
            unclear = pipeline_result.confidence_score == 0

        await session.commit()

    await _increment_counters(job_uuid, failed=failed, unclear=unclear)


async def _mark_batch_row_failed_direct(
    batch_job_id: str, job_result_id: str, error_message: str
) -> None:
    """Best-effort FAILED update for a batch row when Phase-1 DB failure occurs."""
    result_uuid = UUID(job_result_id)
    job_uuid = UUID(batch_job_id)
    async with AsyncSessionLocal() as session:
        jr = await session.get(JobResult, result_uuid)
        if jr is not None and jr.status == JobResultStatus.PENDING:
            jr.status = JobResultStatus.FAILED
            jr.error_message = error_message
            if jr.verification_result_id:
                ver = await session.get(VerificationResult, jr.verification_result_id)
                if ver is not None and ver.status not in (
                    VerificationStatus.COMPLETE, VerificationStatus.FAILED
                ):
                    ver.status = VerificationStatus.FAILED
                    ver.error_message = error_message
            await session.commit()
    await _increment_counters(job_uuid, failed=True)


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

async def _increment_counters(
    job_uuid: UUID,
    failed: bool = False,
    unclear: bool = False,
) -> None:
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
                failed_records=BatchJob.failed_records + (1 if failed else 0),
                unclear_records=BatchJob.unclear_records + (1 if unclear and not failed else 0),
                successful_records=BatchJob.successful_records + (
                    1 if not failed and not unclear else 0
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
                .where(BatchJob.id == job_uuid)
                .values(
                    status=BatchJobStatus.COMPLETE,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            logger.info("Batch job complete", batch_job_id=str(job_uuid))
        await session.commit()
