"""
Shared verification pipeline — imported by both verification_tasks and batch_tasks.

Public surface (importable for tests):
  execute_pipeline   — pure async pipeline (injected services, no DB)
  run_pipeline       — convenience wrapper: wires services and calls execute_pipeline
  PipelineResult     — dataclass returned by execute_pipeline
  EvidenceData       — neutral evidence DTO used in PipelineResult

Internal:
  _PipelineError          — raised when all search queries fail
  _apply_pipeline_result  — writes PipelineResult fields onto a VerificationResult ORM object
"""
import httpx
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.enums import ConfidenceLevel, EvidenceSourceType, TriState, VerificationStatus
from app.models.evidence_source import EvidenceSource
from app.models.verification_result import VerificationResult
from app.services.confidence_service import ConfidenceService
from app.services.evidence_service import EvidenceService
from app.services.llm_service import LLMService
from app.services.search_service import SearchProvider, SearchService

logger = get_logger(__name__)


class _PipelineError(RuntimeError):
    """Raised when the pipeline cannot proceed (e.g. all search queries failed)."""


@dataclass
class EvidenceData:
    """Neutral evidence DTO — decouples the task layer from LLMService internals."""
    url: str
    title: str
    source_type: EvidenceSourceType
    explanation: str


@dataclass
class PipelineResult:
    person_found: TriState
    appears_associated: TriState
    found_on_website: TriState
    company_active: TriState
    email_match: TriState
    confidence_score: int
    confidence_level: ConfidenceLevel
    evidence_sources: list[EvidenceData]
    useful_links: dict[str, str]
    raw_search_data: dict[str, Any] = field(default_factory=dict)


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

    evidence_data = [
        EvidenceData(
            url=src.url,
            title=src.title,
            source_type=src.source_type,
            explanation=src.explanation,
        )
        for src in analysis.evidence_sources
    ]

    return PipelineResult(
        person_found=analysis.person_found,
        appears_associated=analysis.appears_associated,
        found_on_website=analysis.found_on_website,
        company_active=analysis.company_active,
        email_match=analysis.email_match,
        confidence_score=confidence.score,
        confidence_level=confidence.level,
        evidence_sources=evidence_data,
        useful_links=analysis.useful_links,
        raw_search_data={
            "search_queries": search_results.queries_run,
            "serper_raw":     search_results.raw_data,
            "llm_raw_response": analysis.raw_response,
            "confidence_breakdown": confidence.breakdown,
        },
    )


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
