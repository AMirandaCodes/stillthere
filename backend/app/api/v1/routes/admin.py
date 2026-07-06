"""
Admin-only endpoints. Every route here requires is_admin=True on the caller's account.
"""
import math

from fastapi import APIRouter

from app.api.deps import CurrentAdmin, DbSession, PaginationDep
from app.repositories.verification_repository import VerificationRepository
from app.schemas.common import PaginatedResponse
from app.schemas.verification import AdminVerificationSummary

router = APIRouter()


@router.get(
    "/verifications",
    response_model=PaginatedResponse[AdminVerificationSummary],
    summary="List all verifications across all users",
    description=(
        "Returns a paginated list of every verification submitted on the platform, "
        "regardless of which user (or guest) submitted it. Requires is_admin=True."
    ),
)
async def list_all_verifications(
    pagination: PaginationDep,
    db: DbSession,
    _: CurrentAdmin,
) -> PaginatedResponse[AdminVerificationSummary]:
    repo = VerificationRepository(db)
    results, total = await repo.list_all_with_user(
        offset=pagination.offset, limit=pagination.page_size
    )
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
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=math.ceil(total / pagination.page_size) if total and pagination.page_size else 0,
    )
