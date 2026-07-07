from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession, PaginationDep
from app.schemas.common import PaginatedResponse
from app.schemas.company import CompanyResponse
from app.services.company_service import CompanyService

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
    return await CompanyService(db).list_with_counts(
        offset=pagination.offset,
        limit=pagination.page_size,
        page=pagination.page,
        page_size=pagination.page_size,
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
    result = await CompanyService(db).get(company_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )
    return result
