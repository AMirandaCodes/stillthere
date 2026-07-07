"""
Admin-only endpoints. Every route here requires is_admin=True on the caller's account.
"""
from fastapi import APIRouter, Request

from app.api.deps import CurrentAdmin, DbSession, PaginationDep
from app.core.rate_limiting import limiter
from app.schemas.common import PaginatedResponse
from app.schemas.verification import AdminVerificationSummary
from app.services.verification_service import VerificationService

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
@limiter.limit("60/minute")
async def list_all_verifications(
    request: Request,
    pagination: PaginationDep,
    db: DbSession,
    _: CurrentAdmin,
) -> PaginatedResponse[AdminVerificationSummary]:
    return await VerificationService(db).list_all_results(
        offset=pagination.offset, limit=pagination.page_size
    )
