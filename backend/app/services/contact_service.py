"""
ContactService — read layer for contact data.

Extracted so that contacts.py routes comply with the layer convention
(Routes → Services → Repositories) and don't instantiate repos or call
cross-module response builders directly.
"""
import math
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.contact_repository import ContactRepository
from app.schemas.builders import build_summary
from app.schemas.common import PaginatedResponse
from app.schemas.contact import ContactResponse, ContactSummaryResponse
from app.schemas.verification import VerificationSummary


class ContactService:
    def __init__(
        self,
        session: AsyncSession,
        repo: ContactRepository | None = None,
    ) -> None:
        self._repo = repo or ContactRepository(session)

    async def list(
        self,
        offset: int,
        limit: int,
        page: int,
        page_size: int,
        query: str | None = None,
    ) -> PaginatedResponse[ContactSummaryResponse]:
        if query:
            rows, total = await self._repo.search_with_count(
                query, offset=offset, limit=limit
            )
        else:
            rows = await self._repo.list_with_verification_count(offset=offset, limit=limit)
            total = await self._repo.count()

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
            page=page,
            page_size=page_size,
            total_pages=math.ceil(total / page_size) if total else 0,
        )

    async def get(self, contact_id: UUID, user_id: UUID | None = None) -> ContactResponse | None:
        contact = await self._repo.get_with_recent_searches(contact_id)
        if contact is None:
            return None

        # Filter searches to the requesting user so verification results from other
        # users are never returned (AZ-01 / CWE-639). user_id=None means no filter
        # (admin or internal use).
        user_searches = [
            s for s in contact.searches
            if user_id is None or s.user_id == user_id
        ]
        summaries: list[VerificationSummary] = []
        for search in sorted(user_searches, key=lambda s: s.created_at, reverse=True)[:10]:
            latest = search.latest_result
            if latest:
                summaries.append(build_summary(latest))

        return ContactResponse(
            id=contact.id,
            full_name=contact.full_name,
            email=contact.email,
            created_at=contact.created_at,
            recent_verifications=summaries,
        )
