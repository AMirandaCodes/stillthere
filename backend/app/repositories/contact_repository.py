from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.contact import Contact
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.repositories.base import BaseRepository


class ContactRepository(BaseRepository[Contact]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Contact, session)

    # ── Lookups ────────────────────────────────────────────────────────────────

    async def get_by_email(self, email: str) -> Contact | None:
        result = await self.session.execute(
            select(Contact).where(Contact.email == email.lower().strip())
        )
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        result = await self.session.execute(
            select(Contact.id).where(Contact.email == email.lower().strip())
        )
        return result.scalar_one_or_none() is not None

    # ── Creation / dedup ───────────────────────────────────────────────────────

    async def get_or_create_by_email(
        self, full_name: str, email: str
    ) -> tuple[Contact, bool]:
        """
        Dedup on email when provided.
        Returns (contact, was_created).
        Handles the race condition where two concurrent requests try to create
        the same contact by catching IntegrityError and re-fetching.
        """
        existing = await self.get_by_email(email)
        if existing:
            return existing, False

        contact = Contact(full_name=full_name, email=email.lower().strip())
        try:
            return await self.save(contact), True
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_email(email)
            return existing, False  # type: ignore[return-value]

    async def create(self, full_name: str, email: str | None = None) -> Contact:
        """Create a contact without dedup (used when no email is provided)."""
        contact = Contact(
            full_name=full_name,
            email=email.lower().strip() if email else None,
        )
        return await self.save(contact)

    # ── List / search ──────────────────────────────────────────────────────────

    async def list_with_verification_count(
        self, offset: int = 0, limit: int = 20
    ) -> list[tuple[Contact, int]]:
        """
        Return contacts alongside their total verification count.
        Uses a single LEFT JOIN + GROUP BY query — no N+1.
        """
        stmt = (
            select(Contact, func.count(VerificationResult.id).label("total_verifications"))
            .outerjoin(Search, Search.contact_id == Contact.id)
            .outerjoin(VerificationResult, VerificationResult.search_id == Search.id)
            .group_by(Contact.id)
            .order_by(Contact.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = await self.session.execute(stmt)
        return [(contact, count) for contact, count in rows.all()]

    async def search_with_count(
        self, query: str, offset: int = 0, limit: int = 20
    ) -> tuple[list[tuple[Contact, int]], int]:
        """Full-text-style name search. Returns (rows, total_matching)."""
        normalised = query.strip().lower()
        base = (
            select(Contact)
            .where(Contact.normalized_name.ilike(f"%{normalised}%"))
        )
        total = await self.session.scalar(
            select(func.count()).select_from(base.subquery())
        )
        stmt = (
            select(Contact, func.count(VerificationResult.id).label("total_verifications"))
            .outerjoin(Search, Search.contact_id == Contact.id)
            .outerjoin(VerificationResult, VerificationResult.search_id == Search.id)
            .where(Contact.normalized_name.ilike(f"%{normalised}%"))
            .group_by(Contact.id)
            .order_by(Contact.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = await self.session.execute(stmt)
        return [(c, cnt) for c, cnt in rows.all()], total or 0

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(Contact.id)))
        return result.scalar_one()

    # ── Detail (with recent verifications) ────────────────────────────────────

    async def get_with_recent_searches(self, contact_id: UUID) -> Contact | None:
        """
        Load a contact with its most recent searches, each search carrying its
        company and the latest verification result.
        """
        stmt = (
            select(Contact)
            .options(
                selectinload(Contact.searches).options(
                    selectinload(Search.company),
                    selectinload(Search.verification_results),
                )
            )
            .where(Contact.id == contact_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
