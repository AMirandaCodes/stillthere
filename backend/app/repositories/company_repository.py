import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.repositories.base import BaseRepository


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


class CompanyRepository(BaseRepository[Company]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Company, session)

    # ── Lookups ────────────────────────────────────────────────────────────────

    async def get_by_normalized_name(self, normalized_name: str) -> Company | None:
        result = await self.session.execute(
            select(Company).where(Company.normalized_name == normalized_name)
        )
        return result.scalar_one_or_none()

    # ── Creation / dedup ───────────────────────────────────────────────────────

    async def get_or_create(self, name: str) -> tuple[Company, bool]:
        """
        Dedup on normalised name.
        Returns (company, was_created).
        """
        normalised = _normalise(name)
        existing = await self.get_by_normalized_name(normalised)
        if existing:
            return existing, False

        company = Company(name=name)
        try:
            return await self.save(company), True
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_normalized_name(normalised)
            return existing, False  # type: ignore[return-value]

    async def update_web_info(
        self, company_id: UUID, website: str | None, domain: str | None
    ) -> Company | None:
        """
        Persist discovered website / domain after the verification pipeline
        identifies the company's web presence.  Only overwrites null fields —
        does not replace manually-set values.
        """
        company = await self.get_by_id(company_id)
        if not company:
            return None
        if website and not company.website:
            company.website = website
        if domain and not company.domain:
            company.domain = domain
        await self.session.flush()
        return company

    # ── List ───────────────────────────────────────────────────────────────────

    async def list_with_verification_count(
        self, offset: int = 0, limit: int = 20
    ) -> list[tuple[Company, int]]:
        stmt = (
            select(Company, func.count(VerificationResult.id).label("total_verifications"))
            .outerjoin(Search, Search.company_id == Company.id)
            .outerjoin(VerificationResult, VerificationResult.search_id == Search.id)
            .group_by(Company.id)
            .order_by(Company.name.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = await self.session.execute(stmt)
        return [(co, cnt) for co, cnt in rows.all()]

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(Company.id)))
        return result.scalar_one()
