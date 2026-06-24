from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.company import Company
from app.models.contact import Contact
from app.models.enums import SearchSource, VerificationStatus
from app.models.evidence_source import EvidenceSource
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.repositories.base import BaseRepository


class VerificationRepository(BaseRepository[VerificationResult]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(VerificationResult, session)

    # ── Search record ──────────────────────────────────────────────────────────

    async def create_search(
        self,
        contact_id: UUID,
        company_id: UUID,
        submitted_email: str | None,
        source: SearchSource,
        user_id: UUID | None = None,
        batch_job_id: UUID | None = None,
    ) -> Search:
        search = Search(
            contact_id=contact_id,
            company_id=company_id,
            submitted_email=submitted_email,
            source=source,
            user_id=user_id,
            batch_job_id=batch_job_id,
        )
        self.session.add(search)
        await self.session.flush()
        await self.session.refresh(search)
        return search

    # ── VerificationResult records ─────────────────────────────────────────────

    async def create_result(self, search_id: UUID) -> VerificationResult:
        result = VerificationResult(
            search_id=search_id,
            status=VerificationStatus.PENDING,
        )
        self.session.add(result)
        await self.session.flush()
        await self.session.refresh(result)
        return result

    async def get_by_id_with_relations(self, result_id: UUID) -> VerificationResult | None:
        """
        Full load for the detail view: search → contact + company, plus all evidence.
        Uses selectinload (two extra SELECT statements) rather than joinedload to
        avoid the Cartesian product that joinedload produces with multiple collections.
        """
        stmt = (
            select(VerificationResult)
            .options(
                selectinload(VerificationResult.search).options(
                    selectinload(Search.contact),
                    selectinload(Search.company),
                ),
                selectinload(VerificationResult.evidence_sources),
            )
            .where(VerificationResult.id == result_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_with_relations(
        self, offset: int = 0, limit: int = 20
    ) -> tuple[list[VerificationResult], int]:
        """
        Paginated list for the history view.
        Loads search → contact + company (no evidence sources — summary only).
        Returns (results, total_count) in one round-trip pair.
        """
        total: int = await self.session.scalar(
            select(func.count(VerificationResult.id))
        ) or 0

        stmt = (
            select(VerificationResult)
            .options(
                selectinload(VerificationResult.search).options(
                    selectinload(Search.contact),
                    selectinload(Search.company),
                )
            )
            .order_by(VerificationResult.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = await self.session.execute(stmt)
        return list(rows.scalars().all()), total

    # ── Updates ────────────────────────────────────────────────────────────────

    async def update_fields(
        self, result_id: UUID, **fields: object
    ) -> VerificationResult | None:
        """
        General-purpose field updater used by both the service layer and the
        Celery task (Phase 4) when writing back pipeline results.
        """
        result = await self.get_by_id(result_id)
        if result is None:
            return None
        for key, value in fields.items():
            setattr(result, key, value)
        await self.session.flush()
        return result

    # ── Evidence sources ───────────────────────────────────────────────────────

    async def add_evidence_source(
        self,
        verification_result_id: UUID,
        url: str,
        title: str | None = None,
        snippet: str | None = None,
        explanation: str | None = None,
        source_type: str = "search_result",
    ) -> EvidenceSource:
        evidence = EvidenceSource(
            verification_result_id=verification_result_id,
            url=url,
            title=title,
            snippet=snippet,
            explanation=explanation,
            source_type=source_type,
        )
        self.session.add(evidence)
        await self.session.flush()
        await self.session.refresh(evidence)
        return evidence
