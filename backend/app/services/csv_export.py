"""
CSV streaming export for batch results.

Extracted from BatchService (SP-01) so column-order and encoding changes have a
dedicated module separate from the upload/orchestration layer.
"""
import csv
import io
from typing import AsyncGenerator
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.job_result import JobResult
from app.models.search import Search
from app.models.verification_result import VerificationResult

logger = get_logger(__name__)

_CSV_EXPORT_PAGE_SIZE = 100

_COL_HEADERS = [
    "row_number", "name", "company", "email",
    "status",
    "person_found", "appears_associated", "found_on_website",
    "company_active", "email_match",
    "confidence_score", "confidence_level",
    "error_message",
]


def _csv_row(jr: JobResult) -> list:
    """Build one CSV row from a fully-loaded JobResult."""
    raw = jr.raw_csv_row or {}
    vr = jr.verification_result
    return [
        jr.row_number,
        raw.get("name", ""),
        raw.get("company", ""),
        raw.get("email", ""),
        jr.status.value,
        vr.person_found.value       if vr else "",
        vr.appears_associated.value if vr else "",
        vr.found_on_website.value   if vr else "",
        vr.company_active.value     if vr else "",
        vr.email_match.value        if vr else "",
        vr.confidence_score         if vr else "",
        vr.confidence_level.value   if vr else "",
        jr.error_message or (vr.error_message if vr else "") or "",
    ]


async def export_csv_stream(
    job_id: UUID,
    session_factory=None,
) -> AsyncGenerator[bytes, None]:
    """
    Async generator that yields CSV bytes in pages of 100 rows.

    Opens its own DB session so streaming works after the route handler's
    injected session has closed (FastAPI StreamingResponse body is sent
    after the route coroutine returns).

    session_factory: override AsyncSessionLocal for testing — pass an
    asynccontextmanager-wrapped fake session to avoid touching the real DB.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(_COL_HEADERS)
    yield buf.getvalue().encode()
    buf.truncate(0)
    buf.seek(0)

    offset = 0
    factory = session_factory or AsyncSessionLocal
    async with factory() as session:
        while True:
            stmt = (
                select(JobResult)
                .where(JobResult.batch_job_id == job_id)
                .options(
                    selectinload(JobResult.verification_result).options(
                        selectinload(VerificationResult.search).options(
                            selectinload(Search.contact),
                            selectinload(Search.company),
                        )
                    )
                )
                .order_by(JobResult.row_number)
                .offset(offset)
                .limit(_CSV_EXPORT_PAGE_SIZE)
            )
            try:
                page_rows = list((await session.execute(stmt)).scalars().all())
            except Exception as exc:
                logger.error(
                    "CSV export DB error — truncating stream",
                    job_id=str(job_id),
                    offset=offset,
                    error=str(exc),
                )
                break
            if not page_rows:
                break

            for job_result in page_rows:
                writer.writerow(_csv_row(job_result))

            yield buf.getvalue().encode()
            buf.truncate(0)
            buf.seek(0)
            offset += _CSV_EXPORT_PAGE_SIZE
            if len(page_rows) < _CSV_EXPORT_PAGE_SIZE:
                break
