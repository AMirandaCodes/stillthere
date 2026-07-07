"""
CompanyService — read layer for company data.

Extracted so that companies.py routes comply with the layer convention
(Routes → Services → Repositories) and don't instantiate repos directly.
"""
import math
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.company_repository import CompanyRepository
from app.schemas.common import PaginatedResponse
from app.schemas.company import CompanyResponse


class CompanyService:
    def __init__(
        self,
        session: AsyncSession,
        repo: CompanyRepository | None = None,
    ) -> None:
        self._repo = repo or CompanyRepository(session)

    async def list_with_counts(
        self,
        offset: int,
        limit: int,
        page: int,
        page_size: int,
    ) -> PaginatedResponse[CompanyResponse]:
        rows = await self._repo.list_with_verification_count(offset=offset, limit=limit)
        total = await self._repo.count()
        items = [
            CompanyResponse(
                id=company.id,
                name=company.name,
                domain=company.domain,
                website=company.website,
                total_verifications=count,
                created_at=company.created_at,
            )
            for company, count in rows
        ]
        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=math.ceil(total / page_size) if total else 0,
        )

    async def get(self, company_id: UUID) -> CompanyResponse | None:
        company = await self._repo.get_by_id(company_id)
        if company is None:
            return None
        total_verifications = await self._repo.get_verification_count(company_id)
        return CompanyResponse(
            id=company.id,
            name=company.name,
            domain=company.domain,
            website=company.website,
            total_verifications=total_verifications,
            created_at=company.created_at,
        )
