"""
Celery task and pipeline orchestration for single-contact verification.

Public surface (importable for tests):
  run_verification   — Celery task (sync wrapper; acks_late=True)
  execute_pipeline   — pure async pipeline (injected services, no DB)
  run_pipeline       — convenience wrapper: wires services and calls execute_pipeline
  PipelineResult     — dataclass returned by execute_pipeline

Internal:
  _run_verification_async — DB orchestrator called by run_verification
  _apply_pipeline_result  — writes PipelineResult fields onto a VerificationResult ORM object
  _PipelineError          — raised when all search queries fail

Pipeline stages:
  1. SearchPhase  — Serper.dev queries (SearchService)
  2. ScrapePhase  — page fetching + text extraction (EvidenceService)
  3. LLMPhase     — Claude Haiku evidence analysis (LLMService)
  4. ScorePhase   — deterministic 0-100 scoring (ConfidenceService)

Idempotency (checked at task entry):
  COMPLETE → skip (return immediately)
  FAILED   → skip (respect explicit terminal state)
  PENDING  → normal first run
  RUNNING  → crash-recovery path: delete partial evidence, restart from scratch

acks_late=True is already set in celery_app.conf so the broker message is only
acked on successful return.  If the worker crashes mid-task the message is
re-queued and the RUNNING-state idempotency guard handles the clean restart.
"""
import asyncio
import traceback
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import format_exc_message
from app.db.session import TaskSessionLocal as AsyncSessionLocal
from app.models.enums import ConfidenceLevel, TriState, VerificationStatus
from app.models.evidence_source import EvidenceSource
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.services.confidence_service import ConfidenceService
from app.services.evidence_service import EvidenceService
from app.services.llm_service import LLMEvidenceSource, LLMService
from app.services.search_service import SearchProvider, SearchService
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


class _PipelineError(RuntimeError):
    """Raised when the pipeline cannot proceed (e.g. all search queries failed)."""


# ── Public data contract ───────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    person_found: TriState
    appears_associated: TriState
    found_on_website: TriState
    company_active: TriState
    email_match: TriState
    confidence_score: int
    confidence_level: ConfidenceLevel
    evidence_sources: list[LLMEvidenceSource]
    useful_links: dict[str, str]
    raw_search_data: dict[str, Any] = field(default_factory=dict)


# ── Pure pipeline function ─────────────────────────────────────────────────────

async def execute_pipeline(
    name: str,
    company: str,
    email: str | None,
    *,
    search_service: SearchProvider,
    evidence_service: EvidenceService,
    llm_service: LLMService,
    confidence_service: ConfidenceService,
) -> PipelineResult:
    """
    Run the full verification pipeline with injected services.

    No database access — all I/O is through the service interfaces.
    Designed for independent testability: pass mock services to exercise
    the pipeline without hitting any external API or DB.

    Raises _PipelineError if all search queries fail (bad API key, etc.).
    """
    # ── Stage 1: Search ────────────────────────────────────────────────────────
    search_results = await search_service.search(name, company, email)

    if not search_results.queries_run:
        raise _PipelineError(
            "All search queries failed — verify SERPER_API_KEY is correct"
        )

    logger.info("SearchPhase complete", hits=search_results.total_hits)

    # ── Stage 2: Scrape ────────────────────────────────────────────────────────
    urls = [hit.url for hit in search_results.hits]
    pages = await evidence_service.fetch_pages(urls)
    fetched_ok = sum(1 for p in pages if p.fetch_ok)
    logger.info("ScrapePhase complete", pages_fetched=fetched_ok, pages_total=len(pages))

    # ── Stage 3: LLM analysis ──────────────────────────────────────────────────
    analysis = await llm_service.analyse(name, company, email, search_results, pages)
    logger.info(
        "LLMPhase complete",
        person_found=analysis.person_found,
        appears_associated=analysis.appears_associated,
        sources=len(analysis.evidence_sources),
    )

    # ── Stage 4: Confidence scoring ────────────────────────────────────────────
    tri_states = {
        "person_found":       analysis.person_found,
        "appears_associated": analysis.appears_associated,
        "found_on_website":   analysis.found_on_website,
        "company_active":     analysis.company_active,
        "email_match":        analysis.email_match,
    }
    source_types = [src.source_type for src in analysis.evidence_sources]
    confidence = confidence_service.score(tri_states, source_types)
    logger.info("ScorePhase complete", score=confidence.score, level=confidence.level)

    return PipelineResult(
        person_found=analysis.person_found,
        appears_associated=analysis.appears_associated,
        found_on_website=analysis.found_on_website,
        company_active=analysis.company_active,
        email_match=analysis.email_match,
        confidence_score=confidence.score,
        confidence_level=confidence.level,
        evidence_sources=analysis.evidence_sources,
        useful_links=analysis.useful_links,
        raw_search_data={
            "search_queries": search_results.queries_run,
            "serper_raw":     search_results.raw_data,
            "llm_raw_response": analysis.raw_response,
            "confidence_breakdown": confidence.breakdown,
        },
    )


# ── Shared pipeline helpers (also used by batch_tasks) ────────────────────────

async def run_pipeline(name: str, company: str, email: str | None) -> PipelineResult:
    """Wire up services and run the pipeline. Used by both single and batch tasks."""
    settings = get_settings()
    async with httpx.AsyncClient() as http_client:
        return await execute_pipeline(
            name=name,
            company=company,
            email=email,
            search_service=SearchService(
                api_key=settings.SERPER_API_KEY,
                http_client=http_client,
            ),
            evidence_service=EvidenceService(http_client=http_client),
            llm_service=LLMService(api_key=settings.ANTHROPIC_API_KEY),
            confidence_service=ConfidenceService(),
        )


def _apply_pipeline_result(
    result: VerificationResult,
    pipeline: PipelineResult,
    session: AsyncSession,
    result_uuid: UUID,
) -> None:
    """Write all PipelineResult fields onto result and add EvidenceSource rows to session."""
    result.status = VerificationStatus.COMPLETE
    result.person_found = pipeline.person_found
    result.appears_associated = pipeline.appears_associated
    result.found_on_website = pipeline.found_on_website
    result.company_active = pipeline.company_active
    result.email_match = pipeline.email_match
    result.confidence_score = pipeline.confidence_score
    result.confidence_level = pipeline.confidence_level
    result.useful_links = pipeline.useful_links
    result.raw_search_data = pipeline.raw_search_data
    for src in pipeline.evidence_sources:
        session.add(
            EvidenceSource(
                verification_result_id=result_uuid,
                url=src.url,
                title=src.title or None,
                snippet=None,
                explanation=src.explanation or None,
                source_type=src.source_type,
            )
        )


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
    async with AsyncSessionLocal() as session:
        result = await session.get(VerificationResult, result_uuid)
        if result is None:
            logger.error("VerificationResult not found — aborting", result_id=result_id)
            return

        if result.status == VerificationStatus.COMPLETE:
            logger.info("Already complete — skipping", result_id=result_id)
            return
        if result.status == VerificationStatus.FAILED:
            logger.info("Already failed — skipping", result_id=result_id)
            return

        # RUNNING = crash recovery or service set it before task started:
        # delete any partial evidence and restart cleanly.
        if result.status == VerificationStatus.RUNNING:
            await session.execute(
                delete(EvidenceSource).where(
                    EvidenceSource.verification_result_id == result_uuid
                )
            )
            logger.info("Crash recovery: cleared partial evidence", result_id=result_id)

        result.status = VerificationStatus.RUNNING
        await session.commit()

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
        error_msg = format_exc_message(exc)
        logger.error(
            "Pipeline execution failed",
            result_id=result_id,
            error=error_msg,
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
            _apply_pipeline_result(result, pipeline_result, session, result_uuid)

        await session.commit()
        logger.info(
            "Verification complete",
            result_id=result_id,
            status=result.status,
            score=result.confidence_score if pipeline_result else None,
        )


# ── Celery task ────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="verification.run",
    acks_late=True,  # inherited from celery_app.conf but explicit here for clarity
)
def run_verification(self, result_id: str) -> None:
    """
    Sync Celery entry point. Delegates to the async orchestrator via asyncio.run().

    Only raises if the DB is completely unavailable (asyncio.run raises).
    In that case acks_late ensures the message is re-queued automatically.
    All application-level errors are handled inside _run_verification_async.
    """
    asyncio.run(_run_verification_async(result_id))
