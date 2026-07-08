"""
VerificationService — the orchestration layer for single-contact verification.

Responsibilities:
  1. Dedup / create Contact and Company records
  2. Create Search + VerificationResult (status=pending)
  3. Commit so the Celery worker can read the record immediately
  4. Dispatch the Celery task; on dispatch failure set status=failed
  5. Provide read methods for the API routes

Response building (ORM → schema) lives in app.schemas.builders.
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
from app.schemas.builders import build_result_response, build_summary
from app.schemas.common import PaginatedResponse
from app.schemas.verification import (
    AdminVerificationSummary,
    VerificationCreate,
    VerificationJobResponse,
    VerificationResultResponse,
    VerificationSummary,
)
from app.tasks.verification_tasks import run_verification

logger = get_logger(__name__)


class VerificationService:
    def __init__(
        self,
        session: AsyncSession,
        repo: VerificationRepository | None = None,
        contact_repo: ContactRepository | None = None,
        company_repo: CompanyRepository | None = None,
    ) -> None:
        self._session = session
        self._contacts = contact_repo or ContactRepository(session)
        self._companies = company_repo or CompanyRepository(session)
        self._verifications = repo or VerificationRepository(session)

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
            await self._session.commit()
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
            await self._session.commit()

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
        return build_result_response(result)

    async def list_results(
        self, offset: int, limit: int, user_id: UUID | None = None
    ) -> PaginatedResponse[VerificationSummary]:
        results, total = await self._verifications.list_with_relations(offset, limit, user_id=user_id)
        return PaginatedResponse.build(
            items=[build_summary(r) for r in results],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def list_all_results(
        self, offset: int, limit: int
    ) -> PaginatedResponse[AdminVerificationSummary]:
        """Admin-only: paginated list of every verification across all users."""
        results, total = await self._verifications.list_all_with_user(offset, limit)
        items = [
            AdminVerificationSummary(
                id=r.id,
                search_id=r.search_id,
                status=r.status,
                full_name=r.search.contact.full_name,
                company_name=r.search.company.name,
                work_email=r.search.submitted_email,
                user_email=r.search.user.email if r.search.user else None,
                confidence_score=r.confidence_score,
                confidence_level=r.confidence_level,
                created_at=r.created_at,
            )
            for r in results
        ]
        return PaginatedResponse.build(items=items, total=total, offset=offset, limit=limit)
