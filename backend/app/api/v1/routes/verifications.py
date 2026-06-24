import math
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession, PaginationDep
from app.schemas.common import PaginatedResponse
from app.schemas.verification import (
    VerificationCreate,
    VerificationJobResponse,
    VerificationResultResponse,
    VerificationSummary,
)
from app.services.verification_service import VerificationService

router = APIRouter()


@router.post(
    "",
    response_model=VerificationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a verification request",
    description=(
        "Creates a background verification job for the given contact and company. "
        "Returns immediately with a job ID. Poll GET /{id} to retrieve results."
    ),
)
async def submit_verification(
    payload: VerificationCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> VerificationJobResponse:
    service = VerificationService(db)
    return await service.submit(payload, user_id=current_user.id)


@router.get(
    "",
    response_model=PaginatedResponse[VerificationSummary],
    summary="List all verifications (shared history)",
)
async def list_verifications(
    pagination: PaginationDep,
    db: DbSession,
    _: CurrentUser,
) -> PaginatedResponse[VerificationSummary]:
    service = VerificationService(db)
    return await service.list_results(
        offset=pagination.offset, limit=pagination.page_size
    )


@router.get(
    "/{verification_id}",
    response_model=VerificationResultResponse,
    summary="Get a verification result",
    description=(
        "Returns the current state of a verification. "
        "While status is 'pending' or 'running', poll this endpoint until "
        "status is 'complete' or 'failed'."
    ),
)
async def get_verification(
    verification_id: UUID,
    db: DbSession,
    _: CurrentUser,
) -> VerificationResultResponse:
    service = VerificationService(db)
    result = await service.get_result(verification_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification {verification_id} not found",
        )
    return result
