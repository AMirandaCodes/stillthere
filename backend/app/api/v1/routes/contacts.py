import math
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, PaginationDep
from app.models.search import Search
from app.repositories.contact_repository import ContactRepository
from app.schemas.common import PaginatedResponse
from app.schemas.contact import ContactResponse, ContactSummaryResponse
from app.schemas.verification import VerificationSummary
from app.services.verification_service import _build_summary

router = APIRouter()


@router.get(
    "",
    response_model=PaginatedResponse[ContactSummaryResponse],
    summary="List all contacts",
)
async def list_contacts(
    pagination: PaginationDep,
    db: DbSession,
    _: CurrentUser,
    q: str | None = Query(None, description="Filter by name (partial match)"),
) -> PaginatedResponse[ContactSummaryResponse]:
    repo = ContactRepository(db)

    if q:
        rows, total = await repo.search_with_count(
            q, offset=pagination.offset, limit=pagination.page_size
        )
    else:
        rows = await repo.list_with_verification_count(
            offset=pagination.offset, limit=pagination.page_size
        )
        total = await repo.count()

    items = [
        ContactSummaryResponse(
            id=contact.id,
            full_name=contact.full_name,
            email=contact.email,
            total_verifications=count,
            created_at=contact.created_at,
        )
        for contact, count in rows
    ]
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=math.ceil(total / pagination.page_size) if total else 0,
    )


@router.get(
    "/{contact_id}",
    response_model=ContactResponse,
    summary="Get a contact with recent verifications",
)
async def get_contact(
    contact_id: UUID,
    db: DbSession,
    _: CurrentUser,
) -> ContactResponse:
    repo = ContactRepository(db)
    contact = await repo.get_with_recent_searches(contact_id)

    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    # Flatten searches → latest verification result per search → VerificationSummary
    summaries: list[VerificationSummary] = []
    for search in sorted(contact.searches, key=lambda s: s.created_at, reverse=True)[:10]:
        latest = search.latest_result
        if latest:
            # Attach the loaded company to the search so _build_summary can read it
            summaries.append(_build_summary(latest))

    return ContactResponse(
        id=contact.id,
        full_name=contact.full_name,
        email=contact.email,
        created_at=contact.created_at,
        recent_verifications=summaries,
    )
