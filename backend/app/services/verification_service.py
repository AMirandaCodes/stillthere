"""
VerificationService — the orchestration layer for single-contact verification.

Responsibilities:
  1. Dedup / create Contact and Company records
  2. Create Search + VerificationResult (status=pending)
  3. Commit so the Celery worker can read the record immediately
  4. Dispatch the Celery task; on dispatch failure set status=failed
  5. Provide read methods for the API routes

Intentionally knows nothing about HTTP: no FastAPI imports, no Request objects.
"""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.enums import SearchSource, VerificationStatus
from app.models.verification_result import VerificationResult
from app.repositories.contact_repository import ContactRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.verification_repository import VerificationRepository
from app.schemas.common import PaginatedResponse
from app.schemas.verification import (
    EvidenceSourceResponse,
    VerificationCreate,
    VerificationJobResponse,
    VerificationResultResponse,
    VerificationSummary,
)
from app.tasks.verification_tasks import run_verification

logger = get_logger(__name__)


# ── Private response builders ──────────────────────────────────────────────────

def _build_result_response(result: VerificationResult) -> VerificationResultResponse:
    """
    Map a fully-loaded VerificationResult ORM object to its API response schema.
    Requires result.search, result.search.contact, result.search.company, and
    result.evidence_sources to all be eagerly loaded before calling.
    """
    search = result.search
    return VerificationResultResponse(
        id=result.id,
        search_id=result.search_id,
        status=result.status,
        full_name=search.contact.full_name,
        company_name=search.company.name,
        work_email=search.submitted_email,
        person_found=result.person_found,
        appears_associated=result.appears_associated,
        found_on_website=result.found_on_website,
        company_active=result.company_active,
        email_match=result.email_match,
        confidence_score=result.confidence_score,
        confidence_level=result.confidence_level,
        evidence_sources=[
            EvidenceSourceResponse(
                id=e.id,
                url=e.url,
                title=e.title,
                snippet=e.snippet,
                explanation=e.explanation,
                source_type=e.source_type,
                collected_at=e.collected_at,
            )
            for e in result.evidence_sources
        ],
        useful_links=result.useful_links or {},
        error_message=result.error_message,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


def _build_summary(result: VerificationResult) -> VerificationSummary:
    """Lightweight summary for list views. Requires search→contact/company loaded."""
    return VerificationSummary(
        id=result.id,
        search_id=result.search_id,
        status=result.status,
        full_name=result.search.contact.full_name,
        company_name=result.search.company.name,
        confidence_score=result.confidence_score,
        confidence_level=result.confidence_level,
        created_at=result.created_at,
    )


# ── Service ────────────────────────────────────────────────────────────────────

class VerificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._contacts = ContactRepository(session)
        self._companies = CompanyRepository(session)
        self._verifications = VerificationRepository(session)

    # ── Write ──────────────────────────────────────────────────────────────────

    async def submit(
        self,
        data: VerificationCreate,
        user_id: UUID | None = None,
    ) -> VerificationJobResponse:
        """
        Create all DB records, commit, then dispatch the Celery task.

        Committing before dispatch ensures the worker can always load the
        VerificationResult record — even if it starts within milliseconds of
        the task being enqueued.
        """
        # ── Step 1: dedup / create Contact ────────────────────────────────────
        if data.work_email:
            contact, _ = await self._contacts.get_or_create_by_email(
                data.full_name, data.work_email
            )
        else:
            contact = await self._contacts.create(data.full_name)

        # ── Step 2: dedup / create Company ────────────────────────────────────
        company, _ = await self._companies.get_or_create(data.company_name)

        # ── Step 3: create Search record ──────────────────────────────────────
        search = await self._verifications.create_search(
            contact_id=contact.id,
            company_id=company.id,
            submitted_email=data.work_email,
            source=SearchSource.SINGLE,
            user_id=user_id,
        )

        # ── Step 4: create VerificationResult (pending) ───────────────────────
        result = await self._verifications.create_result(search_id=search.id)

        # ── Step 5: commit so the worker can read the record ──────────────────
        await self._session.commit()

        # ── Step 6: dispatch Celery task ──────────────────────────────────────
        try:
            task = run_verification.delay(str(result.id))
            # Store task ID only; the task itself transitions status PENDING → RUNNING.
            await self._verifications.update_fields(
                result.id,
                celery_task_id=str(task.id),
            )
            logger.info(
                "Verification task dispatched",
                result_id=str(result.id),
                task_id=str(task.id),
            )
        except Exception as exc:
            logger.error(
                "Failed to dispatch verification task — marking as failed",
                result_id=str(result.id),
                error=str(exc),
            )
            await self._verifications.update_fields(
                result.id,
                status=VerificationStatus.FAILED,
                error_message="Failed to queue verification task. Check Celery worker logs.",
            )

        return VerificationJobResponse(
            search_id=search.id,
            verification_id=result.id,
            status=VerificationStatus.PENDING,
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    async def get_result(
        self, verification_id: UUID
    ) -> VerificationResultResponse | None:
        result = await self._verifications.get_by_id_with_relations(verification_id)
        if result is None:
            return None
        return _build_result_response(result)

    async def list_results(
        self, offset: int, limit: int, user_id: UUID | None = None
    ) -> PaginatedResponse[VerificationSummary]:
        results, total = await self._verifications.list_with_relations(offset, limit, user_id=user_id)
        return PaginatedResponse.build(
            items=[_build_summary(r) for r in results],
            total=total,
            offset=offset,
            limit=limit,
        )
