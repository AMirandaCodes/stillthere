"""
Celery task for single-contact verification.

Pipeline logic lives in app.tasks.pipeline (execute_pipeline, run_pipeline, PipelineResult).
This module owns only the DB orchestration wrapper.

Public surface (importable for tests):
  run_verification        — Celery task (sync wrapper; acks_late=True)
  _run_verification_async — async DB orchestrator (for integration tests)
  _check_and_set_running  — idempotency guard (for integration tests)

Idempotency (checked at task entry):
  COMPLETE → skip (return immediately)
  FAILED   → skip (respect explicit terminal state)
  PENDING  → normal first run
  RUNNING  → crash-recovery path: delete partial evidence, restart from scratch

acks_late=True is set in celery_app.conf; the broker message is only acked on
successful return. If the worker crashes mid-task the message is re-queued and
the RUNNING-state idempotency guard handles the clean restart.
"""
import asyncio
import traceback
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import TaskSessionLocal as AsyncSessionLocal
from app.models.enums import VerificationStatus
from app.models.evidence_source import EvidenceSource
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.tasks.celery_app import celery_app
from app.tasks.pipeline import PipelineResult, PipelineError, run_pipeline
from app.tasks.result_mapper import apply_pipeline_result

logger = get_logger(__name__)


def _user_error_message(exc: Exception) -> str:
    if isinstance(exc, PipelineError):
        return "Search failed. The service may be temporarily unavailable — please try again."
    return "An unexpected error occurred during verification."


# ── DB orchestrator helpers ────────────────────────────────────────────────────

async def _check_and_set_running(result_uuid: UUID, result_id: str) -> bool:
    """
    Idempotency guard + RUNNING transition for a single verification.

    Returns False if the task should be skipped (result not found or already terminal).
    Handles crash recovery: if status is RUNNING, deletes partial evidence and restarts.
    """
    async with AsyncSessionLocal() as session:
        result = await session.get(VerificationResult, result_uuid)
        if result is None:
            logger.error("VerificationResult not found — aborting", result_id=result_id)
            return False

        if result.status in (VerificationStatus.COMPLETE, VerificationStatus.FAILED):
            logger.info("Already terminal — skipping", result_id=result_id, status=result.status)
            return False

        if result.status == VerificationStatus.RUNNING:
            await session.execute(
                delete(EvidenceSource).where(
                    EvidenceSource.verification_result_id == result_uuid
                )
            )
            logger.info("Crash recovery: cleared partial evidence", result_id=result_id)

        result.status = VerificationStatus.RUNNING
        await session.commit()
    return True


# ── DB orchestrator ────────────────────────────────────────────────────────────

async def _run_verification_async(result_id: str) -> None:
    """
    DB orchestrator: idempotency check → run pipeline → write results.

    Uses three separate DB sessions to keep each phase's transaction minimal:
      Session 1: idempotency + set RUNNING (committed immediately)
      Session 2: load context for pipeline (read-only)
      Session 3: write final results / FAILED state

    Only raises if the DB itself is unavailable (session 1 open fails).
    Application-level errors (bad API key, no results, etc.) are caught and
    stored as status=FAILED without re-raising, so the Celery message is acked.
    """
    result_uuid = UUID(result_id)

    # ── Phase 1: Idempotency + set RUNNING ────────────────────────────────────
    if not await _check_and_set_running(result_uuid, result_id):
        return

    # ── Phase 2: Load search context (read-only) ──────────────────────────────
    async with AsyncSessionLocal() as session:
        stmt = (
            select(VerificationResult)
            .options(
                selectinload(VerificationResult.search).options(
                    selectinload(Search.contact),
                    selectinload(Search.company),
                )
            )
            .where(VerificationResult.id == result_uuid)
        )
        loaded = (await session.execute(stmt)).scalar_one_or_none()
        if loaded is None:
            return

        name = loaded.search.contact.full_name
        company = loaded.search.company.name
        email = loaded.search.submitted_email

    # ── Phase 3: Run pipeline (no DB) ─────────────────────────────────────────
    pipeline_result: PipelineResult | None = None
    error_msg: str | None = None

    try:
        pipeline_result = await run_pipeline(name, company, email)
    except Exception as exc:
        error_msg = _user_error_message(exc)
        logger.error(
            "Pipeline execution failed",
            result_id=result_id,
            exc_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
        )

    # ── Phase 4: Write results ─────────────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        result = await session.get(VerificationResult, result_uuid)
        if result is None:
            return

        if pipeline_result is None:
            result.status = VerificationStatus.FAILED
            result.error_message = error_msg
        else:
            apply_pipeline_result(result, pipeline_result, session, result_uuid)

        await session.commit()
        logger.info(
            "Verification complete",
            result_id=result_id,
            status=result.status,
            score=result.confidence_score if pipeline_result else None,
        )


# ── Celery task ────────────────────────────────────────────────────────────────

async def _mark_failed_direct(result_id: str, error_message: str) -> None:
    """Best-effort FAILED update when Phase-1 DB failure prevents normal orchestration."""
    result_uuid = UUID(result_id)
    async with AsyncSessionLocal() as session:
        result = await session.get(VerificationResult, result_uuid)
        if result is not None and result.status not in (
            VerificationStatus.COMPLETE, VerificationStatus.FAILED
        ):
            result.status = VerificationStatus.FAILED
            result.error_message = error_message
            await session.commit()


@celery_app.task(
    bind=True,
    name="verification.run",
    acks_late=True,  # inherited from celery_app.conf but explicit here for clarity
)
def run_verification(self, result_id: str) -> None:
    """
    Sync Celery entry point. Delegates to the async orchestrator via asyncio.run().

    Application-level errors are handled inside _run_verification_async.
    If the DB itself is unavailable (Phase-1 failure), we attempt a best-effort
    FAILED update so the result does not stay stuck in PENDING indefinitely.
    """
    try:
        asyncio.run(_run_verification_async(result_id))
    except Exception as exc:
        logger.error("Task-level failure", result_id=result_id, error=str(exc))
        try:
            asyncio.run(_mark_failed_direct(result_id, "Verification could not be processed."))
        except Exception:
            pass
        raise
