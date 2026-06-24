import math
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, PaginationDep
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.repositories.company_repository import CompanyRepository
from app.schemas.common import PaginatedResponse
from app.schemas.company import CompanyResponse

router = APIRouter()


@router.get(
    "",
    response_model=PaginatedResponse[CompanyResponse],
    summary="List all companies",
)
async def list_companies(
    pagination: PaginationDep,
    db: DbSession,
    _: CurrentUser,
) -> PaginatedResponse[CompanyResponse]:
    repo = CompanyRepository(db)
    rows = await repo.list_with_verification_count(
        offset=pagination.offset, limit=pagination.page_size
    )
    total = await repo.count()

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
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=math.ceil(total / pagination.page_size) if total else 0,
    )


@router.get(
    "/{company_id}",
    response_model=CompanyResponse,
    summary="Get a company by ID",
)
async def get_company(
    company_id: UUID,
    db: DbSession,
    _: CurrentUser,
) -> CompanyResponse:
    repo = CompanyRepository(db)
    company = await repo.get_by_id(company_id)

    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )

    total_verifications: int = await db.scalar(
        select(func.count(VerificationResult.id))
        .join(Search, Search.id == VerificationResult.search_id)
        .where(Search.company_id == company_id)
    ) or 0

    return CompanyResponse(
        id=company.id,
        name=company.name,
        domain=company.domain,
        website=company.website,
        total_verifications=total_verifications,
        created_at=company.created_at,
    )
