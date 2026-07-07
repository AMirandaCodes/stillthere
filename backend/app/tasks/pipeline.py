"""
Shared verification pipeline — imported by both verification_tasks and batch_tasks.

Public surface (importable for tests):
  execute_pipeline   — pure async pipeline (injected services, no DB)
  run_pipeline       — convenience wrapper: wires services and calls execute_pipeline
  PipelineResult     — dataclass returned by execute_pipeline
  PipelineServices   — groups the four service dependencies for execute_pipeline
  EvidenceData       — neutral evidence DTO used in PipelineResult
  PipelineError      — raised when all search queries fail
  apply_pipeline_result — writes PipelineResult fields onto a VerificationResult ORM object
"""
import httpx
import redis.asyncio as aioredis
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.enums import ConfidenceLevel, EvidenceSourceType, TriState, VerificationStatus
from app.models.evidence_source import EvidenceSource
from app.models.verification_result import VerificationResult
from app.services.cache_service import CacheService
from app.services.confidence_service import ConfidenceService
from app.services.evidence_service import EvidenceService
from app.services.llm_service import LLMService
from app.services.search_service import SearchProvider, SearchService

logger = get_logger(__name__)

# Client-level default timeout: safety net for any call that omits a per-call timeout.
# Per-call values (Serper 15 s, page fetch 10 s, LLM 30 s) still take precedence.
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class PipelineError(RuntimeError):
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


@dataclass
class PipelineServices:
    """Groups the four injected service dependencies for execute_pipeline."""
    search: SearchProvider
    evidence: EvidenceService
    llm: LLMService
    confidence: ConfidenceService


async def execute_pipeline(
    name: str,
    company: str,
    email: str | None,
    services: PipelineServices,
) -> PipelineResult:
    """
    Run the full verification pipeline with injected services.

    No database access — all I/O is through the service interfaces.
    Designed for independent testability: pass mock services to exercise
    the pipeline without hitting any external API or DB.

    Raises PipelineError if all search queries fail (bad API key, etc.).
    """
    # ── Stage 1: Search ────────────────────────────────────────────────────────
    search_results = await services.search.search(name, company, email)

    if not search_results.queries_run:
        raise PipelineError(
            "All search queries failed — verify SERPER_API_KEY is correct"
        )

    logger.info("SearchPhase complete", hits=search_results.total_hits)

    # ── Stage 2: Scrape ────────────────────────────────────────────────────────
    urls = [hit.url for hit in search_results.hits]
    pages = await services.evidence.fetch_pages(urls)
    pages_fetched = sum(1 for p in pages if p.fetch_ok)
    logger.info("ScrapePhase complete", pages_fetched=pages_fetched, pages_total=len(pages))

    # ── Stage 3: LLM analysis ──────────────────────────────────────────────────
    analysis = await services.llm.analyse(name, company, email, search_results, pages)
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
    confidence = services.confidence.score(tri_states, source_types)
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
    """
    Wire up services and run the pipeline. Used by both single and batch tasks.

    Opens a short-lived Redis connection for the search result cache; closes it
    after the pipeline completes regardless of success or failure.  If Redis is
    unavailable the cache degrades gracefully to a no-op (CacheService contract).
    """
    settings = get_settings()

    redis_client = None
    try:
        redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    except Exception:
        pass  # cache degrades to no-op; pipeline still runs

    try:
        cache = CacheService(redis_client)
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http_client:
            services = PipelineServices(
                search=SearchService(
                    api_key=settings.SERPER_API_KEY,
                    http_client=http_client,
                    cache=cache,
                ),
                evidence=EvidenceService(http_client=http_client),
                llm=LLMService(api_key=settings.ANTHROPIC_API_KEY),
                confidence=ConfidenceService(),
            )
            return await execute_pipeline(name, company, email, services)
    finally:
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass


def apply_pipeline_result(
    result: VerificationResult,
    pipeline_result: PipelineResult,
    session: AsyncSession,
    result_uuid: UUID,
) -> None:
    """Write all PipelineResult fields onto result and add EvidenceSource rows to session."""
    result.status = VerificationStatus.COMPLETE
    result.person_found = pipeline_result.person_found
    result.appears_associated = pipeline_result.appears_associated
    result.found_on_website = pipeline_result.found_on_website
    result.company_active = pipeline_result.company_active
    result.email_match = pipeline_result.email_match
    result.confidence_score = pipeline_result.confidence_score
    result.confidence_level = pipeline_result.confidence_level
    result.useful_links = pipeline_result.useful_links
    result.raw_search_data = pipeline_result.raw_search_data
    for src in pipeline_result.evidence_sources:
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
