"""
BatchService — CSV batch verification orchestration.

Responsibilities:
  - Parse and validate uploaded CSV files (delegated to csv_parser)
  - Create BatchJob, Search, VerificationResult, and JobResult records
  - Dispatch process_batch_job Celery task (commit-before-dispatch pattern)
  - Provide progress polling and paginated result reads
  - Stream results as a CSV export (delegated to csv_export)

CSV format:
  Required columns (case-insensitive): Name, Company
  Optional column: Email

Row handling:
  - Rows with empty Name or Company → JobResult(status=SKIPPED), counted immediately
  - Valid rows → JobResult(status=PENDING), processed by Celery workers
"""
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import normalize_name
from app.models.batch_job import BatchJob
from app.models.company import Company
from app.models.contact import Contact
from app.models.enums import BatchJobStatus, JobResultStatus, SearchSource
from app.models.job_result import JobResult
from app.models.search import Search
from app.models.verification_result import VerificationResult
from app.repositories.contact_repository import ContactRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.verification_repository import VerificationRepository
from app.schemas.batch import BatchJobResponse, JobResultResponse
from app.schemas.builders import build_summary
from app.schemas.common import PaginatedResponse
from app.services.csv_parser import BatchValidationError, clean, parse_csv, validate_columns

logger = get_logger(__name__)

_MAX_CSV_BYTES = 5 * 1024 * 1024  # 5 MB guard
_CHUNK_SIZE = 64 * 1024            # 64 KiB read buffer


# ── Response builder ───────────────────────────────────────────────────────────

def _build_job_result_response(jr: JobResult) -> JobResultResponse:
    """Build JobResultResponse from a fully-loaded JobResult ORM object."""
    vr = jr.verification_result
    return JobResultResponse(
        id=jr.id,
        row_number=jr.row_number,
        status=jr.status,
        error_message=jr.error_message,
        raw_csv_row=jr.raw_csv_row or {},
        verification=build_summary(vr) if vr is not None and vr.search is not None else None,
    )


# ── Service ────────────────────────────────────────────────────────────────────

class BatchService:
    def __init__(
        self,
        session: AsyncSession,
        contact_repo: ContactRepository | None = None,
        company_repo: CompanyRepository | None = None,
        verification_repo: VerificationRepository | None = None,
    ) -> None:
        self._session = session
        self._contacts = contact_repo or ContactRepository(session)
        self._companies = company_repo or CompanyRepository(session)
        self._verifications = verification_repo or VerificationRepository(session)

    # ── Upload ─────────────────────────────────────────────────────────────────

    async def upload(self, file: UploadFile, user_id: UUID) -> BatchJobResponse:
        """
        Parse the CSV, validate, create all DB records, dispatch job task.

        Returns immediately with BatchJobResponse(status=QUEUED).
        Processing happens asynchronously via Celery.
        """
        chunks: list[bytes] = []
        total = 0
        while chunk := await file.read(_CHUNK_SIZE):
            total += len(chunk)
            if total > _MAX_CSV_BYTES:
                raise BatchValidationError("File exceeds the 5 MB size limit.")
            chunks.append(chunk)
        raw = b"".join(chunks)

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
            user_id=user_id,
        )
        self._session.add(batch_job)
        await self._session.flush()
        await self._session.refresh(batch_job)

        # ── Bulk-fetch existing contacts/companies to cut per-row DB calls ──
        # For a 50-row CSV this reduces up to 100 individual SELECTs to 2.
        valid_pairs = [
            (clean(row.get("name", "")), clean(row.get("company", "")), clean(row.get("email", "")))
            for row in rows
        ]
        bulk_emails = {
            email.lower()
            for name, company, email in valid_pairs
            if name and company and email
        }
        contact_by_email: dict[str, Contact] = {}
        if bulk_emails:
            for contact in (await self._session.execute(
                select(Contact).where(Contact.email.in_(bulk_emails))
            )).scalars():
                if contact.email:
                    contact_by_email[contact.email] = contact

        bulk_norm_companies = {
            normalize_name(company)
            for name, company, _ in valid_pairs
            if name and company
        }
        company_by_norm: dict[str, Company] = {}
        if bulk_norm_companies:
            for company in (await self._session.execute(
                select(Company).where(Company.normalized_name.in_(bulk_norm_companies))
            )).scalars():
                company_by_norm[company.normalized_name] = company

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

            # Contact: use pre-fetched cache, fall back to repo on cache miss
            if email:
                email_key = email.lower()
                contact = contact_by_email.get(email_key)
                if contact is None:
                    contact, _ = await self._contacts.get_or_create_by_email(name, email)
                    contact_by_email[email_key] = contact
            else:
                contact = await self._contacts.create(name)

            # Company: use pre-fetched cache, fall back to repo on cache miss
            norm_co = normalize_name(company)
            company_obj = company_by_norm.get(norm_co)
            if company_obj is None:
                company_obj, _ = await self._companies.get_or_create(company)
                company_by_norm[norm_co] = company_obj

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
        await self._dispatch_job(batch_job)
        await self._session.refresh(batch_job)
        return BatchJobResponse.model_validate(batch_job)

    # ── Task dispatch ──────────────────────────────────────────────────────────

    async def _dispatch_job(self, batch_job: BatchJob) -> None:
        """Dispatch the Celery task and store task_id; mark FAILED if dispatch fails."""
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

    # ── Read ───────────────────────────────────────────────────────────────────

    async def get_job(self, job_id: UUID) -> BatchJobResponse | None:
        batch_job = await self._session.get(BatchJob, job_id)
        if batch_job is None:
            return None
        return BatchJobResponse.model_validate(batch_job)

    async def list_jobs(
        self, offset: int, limit: int, user_id: UUID | None = None
    ) -> PaginatedResponse[BatchJobResponse]:
        count_stmt = select(func.count(BatchJob.id))
        list_stmt = (
            select(BatchJob)
            .order_by(BatchJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if user_id is not None:
            count_stmt = count_stmt.where(BatchJob.user_id == user_id)
            list_stmt = list_stmt.where(BatchJob.user_id == user_id)
        total: int = await self._session.scalar(count_stmt) or 0
        jobs = list((await self._session.execute(list_stmt)).scalars().all())
        return PaginatedResponse.build(
            items=[BatchJobResponse.model_validate(j) for j in jobs],
            total=total,
            offset=offset,
            limit=limit,
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
        return PaginatedResponse.build(
            items=[_build_job_result_response(job_result) for job_result in rows],
            total=total,
            offset=offset,
            limit=limit,
        )
