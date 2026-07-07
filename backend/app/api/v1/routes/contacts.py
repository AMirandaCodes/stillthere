from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, PaginationDep
from app.schemas.common import PaginatedResponse
from app.schemas.contact import ContactResponse, ContactSummaryResponse
from app.services.contact_service import ContactService

router = APIRouter()


@router.get(
    "",
    response_model=PaginatedResponse[ContactSummaryResponse],
    summary="List all contacts",
    description=(
        "Returns the platform-wide shared contact directory. "
        "Contacts represent external people being verified and are deduped by email "
        "across all users — they are not per-user private records. "
        "Verification result details in the contact detail endpoint are scoped to the "
        "requesting user."
    ),
)
async def list_contacts(
    pagination: PaginationDep,
    db: DbSession,
    _: CurrentUser,
    q: str | None = Query(None, description="Filter by name (partial match)"),
) -> PaginatedResponse[ContactSummaryResponse]:
    return await ContactService(db).list(
        offset=pagination.offset,
        limit=pagination.page_size,
        page=pagination.page,
        page_size=pagination.page_size,
        query=q,
    )


@router.get(
    "/{contact_id}",
    response_model=ContactResponse,
    summary="Get a contact with recent verifications",
)
async def get_contact(
    contact_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> ContactResponse:
    result = await ContactService(db).get(contact_id, user_id=current_user.id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )
    return result
