"""
BatchService — CSV batch verification orchestration.

Responsibilities:
  - Parse and validate uploaded CSV files
  - Create BatchJob, Search, VerificationResult, and JobResult records
  - Dispatch process_batch_job Celery task (commit-before-dispatch pattern)
  - Provide progress polling and paginated result reads
  - Stream results as a CSV export

CSV format:
  Required columns (case-insensitive): Name, Company
  Optional column: Email

Row handling:
  - Rows with empty Name or Company → JobResult(status=SKIPPED), counted immediately
  - Valid rows → JobResult(status=PENDING), processed by Celery workers
"""
import csv
import io
import math
from typing import AsyncGenerator
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.batch_job import BatchJob
from app.models.enums import BatchJobStatus, JobResultStatus, SearchSource
from app.models.job_result import JobResult
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.repositories.contact_repository import ContactRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.verification_repository import VerificationRepository
from app.schemas.batch import BatchJobResponse, JobResultResponse
from app.schemas.common import PaginatedResponse
from app.schemas.verification import VerificationSummary

logger = get_logger(__name__)

_MAX_CSV_BYTES = 5 * 1024 * 1024  # 5 MB guard
_REQUIRED_COLS = frozenset({"name", "company"})


class BatchValidationError(ValueError):
    """Raised when the uploaded CSV fails structural validation."""


# ── Pure CSV helpers (independently testable) ─────────────────────────────────

def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """
    Parse CSV text into (normalised_headers, rows).

    Headers are lowercased and stripped.  Rows with all-empty values are excluded.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    raw_fields = reader.fieldnames or []
    headers = [h.strip().lower() for h in raw_fields if h is not None]
    rows: list[dict[str, str]] = []
    for row in reader:
        normalised = {
            k.strip().lower(): (v or "").strip()
            for k, v in row.items()
            if k is not None
        }
        if any(normalised.values()):
            rows.append(normalised)
    return headers, rows


def validate_columns(headers: list[str]) -> None:
    """Raise BatchValidationError if required columns are absent."""
    missing = _REQUIRED_COLS - set(headers)
    if missing:
        raise BatchValidationError(
            f"CSV is missing required column(s): {', '.join(sorted(missing))}. "
            "Expected headers: Name, Company (case-insensitive). Email is optional."
        )


def clean(value: str) -> str:
    return value.strip()


# ── Response builder ───────────────────────────────────────────────────────────

def _build_job_result_response(jr: JobResult) -> JobResultResponse:
    """Build JobResultResponse from a fully-loaded JobResult ORM object."""
    vr = jr.verification_result
    verification: VerificationSummary | None = None
    if vr is not None and vr.search is not None:
        verification = VerificationSummary(
            id=vr.id,
            search_id=vr.search_id,
            status=vr.status,
            full_name=vr.search.contact.full_name,
            company_name=vr.search.company.name,
            confidence_score=vr.confidence_score,
            confidence_level=vr.confidence_level,
            created_at=vr.created_at,
        )
    return JobResultResponse(
        id=jr.id,
        row_number=jr.row_number,
        status=jr.status,
        error_message=jr.error_message,
        raw_csv_row=jr.raw_csv_row or {},
        verification=verification,
    )


# ── Service ────────────────────────────────────────────────────────────────────

class BatchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._contacts = ContactRepository(session)
        self._companies = CompanyRepository(session)
        self._verifications = VerificationRepository(session)

    # ── Upload ─────────────────────────────────────────────────────────────────

    async def upload(self, file: UploadFile, user_id: UUID) -> BatchJobResponse:
        """
        Parse the CSV, validate, create all DB records, dispatch job task.

        Returns immediately with BatchJobResponse(status=QUEUED).
        Processing happens asynchronously via Celery.
        """
        raw = await file.read()
        if len(raw) > _MAX_CSV_BYTES:
            raise BatchValidationError("File exceeds the 5 MB size limit.")

        try:
            text = raw.decode("utf-8-sig")  # strip UTF-8 BOM if present
        except UnicodeDecodeError:
            raise BatchValidationError("File must be UTF-8 encoded.")

        headers, rows = parse_csv(text)
        validate_columns(headers)

        settings = get_settings()
        if len(rows) > settings.MAX_BATCH_SIZE:
            raise BatchValidationError(
                f"CSV contains {len(rows)} data rows; the maximum is {settings.MAX_BATCH_SIZE}. "
                "Split into multiple uploads if needed."
            )

        batch_job = BatchJob(
            filename=file.filename or "upload.csv",
            status=BatchJobStatus.QUEUED,
            total_records=len(rows),
        )
        self._session.add(batch_job)
        await self._session.flush()
        await self._session.refresh(batch_job)

        for i, row in enumerate(rows, start=2):  # row 1 = header line
            name = clean(row.get("name", ""))
            company = clean(row.get("company", ""))
            email = clean(row.get("email", "")) or None

            if not name or not company:
                self._session.add(JobResult(
                    batch_job_id=batch_job.id,
                    row_number=i,
                    status=JobResultStatus.SKIPPED,
                    error_message="Row skipped: Name and Company are both required.",
                    raw_csv_row=dict(row),
                ))
                batch_job.processed_records += 1
                continue

            contact = await self._get_or_create_contact(name, email)
            company_obj = await self._get_or_create_company(company)

            search = await self._verifications.create_search(
                contact_id=contact.id,
                company_id=company_obj.id,
                submitted_email=email,
                source=SearchSource.BATCH,
                user_id=user_id,
                batch_job_id=batch_job.id,
            )
            ver_result = await self._verifications.create_result(search_id=search.id)

            self._session.add(JobResult(
                batch_job_id=batch_job.id,
                search_id=search.id,
                verification_result_id=ver_result.id,
                row_number=i,
                status=JobResultStatus.PENDING,
                raw_csv_row=dict(row),
            ))

        await self._session.commit()

        # Dispatch after commit so the worker can always read the records
        from app.tasks.batch_tasks import process_batch_job
        try:
            task = process_batch_job.delay(str(batch_job.id))
            await self._session.execute(
                update(BatchJob)
                .where(BatchJob.id == batch_job.id)
                .values(celery_task_id=str(task.id))
            )
            await self._session.commit()
            logger.info(
                "Batch job dispatched",
                batch_job_id=str(batch_job.id),
                task_id=str(task.id),
            )
        except Exception as exc:
            logger.error(
                "Failed to dispatch batch job",
                batch_job_id=str(batch_job.id),
                error=str(exc),
            )
            await self._session.execute(
                update(BatchJob)
                .where(BatchJob.id == batch_job.id)
                .values(status=BatchJobStatus.FAILED)
            )
            await self._session.commit()

        await self._session.refresh(batch_job)
        return BatchJobResponse.model_validate(batch_job)

    # ── Contact / Company dedup helpers ────────────────────────────────────────

    async def _get_or_create_contact(self, full_name: str, email: str | None):
        if email:
            contact, _ = await self._contacts.get_or_create_by_email(full_name, email)
            return contact
        return await self._contacts.create(full_name)

    async def _get_or_create_company(self, name: str):
        company, _ = await self._companies.get_or_create(name)
        return company

    # ── Read ───────────────────────────────────────────────────────────────────

    async def get_job(self, job_id: UUID) -> BatchJobResponse | None:
        batch_job = await self._session.get(BatchJob, job_id)
        if batch_job is None:
            return None
        return BatchJobResponse.model_validate(batch_job)

    async def list_jobs(
        self, offset: int, limit: int
    ) -> PaginatedResponse[BatchJobResponse]:
        total: int = await self._session.scalar(select(func.count(BatchJob.id))) or 0
        stmt = (
            select(BatchJob)
            .order_by(BatchJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        jobs = list((await self._session.execute(stmt)).scalars().all())
        return PaginatedResponse(
            items=[BatchJobResponse.model_validate(j) for j in jobs],
            total=total,
            page=(offset // limit) + 1 if limit else 1,
            page_size=limit,
            total_pages=math.ceil(total / limit) if total and limit else 0,
        )

    async def get_job_results(
        self, job_id: UUID, offset: int, limit: int
    ) -> PaginatedResponse[JobResultResponse]:
        total: int = await self._session.scalar(
            select(func.count(JobResult.id)).where(JobResult.batch_job_id == job_id)
        ) or 0
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
            .limit(limit)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        return PaginatedResponse(
            items=[_build_job_result_response(jr) for jr in rows],
            total=total,
            page=(offset // limit) + 1 if limit else 1,
            page_size=limit,
            total_pages=math.ceil(total / limit) if total and limit else 0,
        )

    # ── CSV export (streaming async generator) ─────────────────────────────────

    @staticmethod
    async def export_csv_stream(job_id: UUID) -> AsyncGenerator[bytes, None]:
        """
        Async generator that yields CSV bytes in pages of 100 rows.

        Opens its own DB session so streaming works after the route handler's
        injected session has closed (FastAPI StreamingResponse body is sent
        after the route coroutine returns).
        """
        _COL_HEADERS = [
            "row_number", "name", "company", "email",
            "status",
            "person_found", "appears_associated", "found_on_website",
            "company_active", "email_match",
            "confidence_score", "confidence_level",
            "error_message",
        ]
        PAGE = 100
        buf = io.StringIO()
        writer = csv.writer(buf)

        writer.writerow(_COL_HEADERS)
        yield buf.getvalue().encode()
        buf.truncate(0)
        buf.seek(0)

        offset = 0
        async with AsyncSessionLocal() as session:
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
                    .limit(PAGE)
                )
                page_rows = list((await session.execute(stmt)).scalars().all())
                if not page_rows:
                    break

                for jr in page_rows:
                    raw = jr.raw_csv_row or {}
                    vr = jr.verification_result
                    writer.writerow([
                        jr.row_number,
                        raw.get("name", ""),
                        raw.get("company", ""),
                        raw.get("email", ""),
                        jr.status.value,
                        vr.person_found.value if vr else "",
                        vr.appears_associated.value if vr else "",
                        vr.found_on_website.value if vr else "",
                        vr.company_active.value if vr else "",
                        vr.email_match.value if vr else "",
                        vr.confidence_score if vr else "",
                        vr.confidence_level.value if vr else "",
                        jr.error_message or (vr.error_message if vr else "") or "",
                    ])

                yield buf.getvalue().encode()
                buf.truncate(0)
                buf.seek(0)
                offset += PAGE
                if len(page_rows) < PAGE:
                    break
