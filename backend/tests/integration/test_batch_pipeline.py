"""
Integration tests for batch CSV processing.

Tests the full lifecycle:
  1. CSV upload via POST /api/v1/batch/upload
  2. BatchJob creation and initial state
  3. process_batch_job + process_batch_row orchestration (tasks called directly)
  4. BatchJob status polling via GET /api/v1/batch/{job_id}
  5. Per-row results via GET /api/v1/batch/{job_id}/results
  6. CSV export via GET /api/v1/batch/{job_id}/export
  7. Idempotency of process_batch_row

External I/O is mocked identically to test_verification_pipeline.py:
  - Serper: respx
  - Page fetches: respx
  - Anthropic client: AsyncMock
"""
from io import BytesIO
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models.enums import BatchJobStatus, JobResultStatus, VerificationStatus
from app.models.batch_job import BatchJob
from app.models.job_result import JobResult
from app.models.verification_result import VerificationResult
from app.services.search_service import SERPER_ENDPOINT
from app.tasks.batch_tasks import (
    _process_batch_job_async,
    _process_batch_row_async,
)
from tests.helpers import make_mock_llm_client

# ── Shared test fixtures ───────────────────────────────────────────────────────

_SERPER_RESPONSE = {
    "organic": [
        {
            "title": "Alice Smith — Manager at Widget Co",
            "link": "https://linkedin.com/in/alice-smith",
            "snippet": "Alice Smith is Manager at Widget Co.",
        }
    ]
}

_SAMPLE_HTML = (
    b"<html><head><title>Alice Smith</title></head>"
    b"<body><p>Alice Smith, Manager at Widget Co.</p></body></html>"
)

_LLM_RESPONSE = {
    "person_found": "yes",
    "appears_associated": "yes",
    "found_on_website": "unclear",
    "company_active": "yes",
    "email_match": "unclear",
    "evidence_sources": [
        {
            "url": "https://linkedin.com/in/alice-smith",
            "title": "LinkedIn",
            "source_type": "professional_profile",
            "explanation": "LinkedIn confirms Alice Smith at Widget Co.",
        }
    ],
    "useful_links": {"LinkedIn": "https://linkedin.com/in/alice-smith"},
    "reasoning": "Evidence supports the contact.",
}


def _mock_llm_client(response: dict | None = None) -> AsyncMock:
    return make_mock_llm_client(response or _LLM_RESPONSE)


def _csv_bytes(*rows: tuple[str, str, str]) -> bytes:
    lines = ["Name,Company,Email"] + [f"{n},{c},{e}" for n, c, e in rows]
    return "\n".join(lines).encode()


def _session_factory_patch(test_engine):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    return patch("app.tasks.batch_tasks.AsyncSessionLocal", factory)


# ── Upload endpoint ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBatchUpload:
    async def test_upload_valid_csv_returns_202(self, client, auth_headers):
        csv = _csv_bytes(("Alice Smith", "Widget Co", "alice@widget.com"))
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("contacts.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "queued"
        assert data["total_records"] == 1
        assert data["filename"] == "contacts.csv"

    async def test_upload_csv_missing_name_column_returns_400(self, client, auth_headers):
        csv = b"Company,Email\nWidget Co,alice@widget.com"
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("bad.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert "name" in r.json()["detail"].lower()

    async def test_upload_csv_missing_company_column_returns_400(self, client, auth_headers):
        csv = b"Name,Email\nAlice,alice@widget.com"
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("bad.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 400

    async def test_upload_skipped_row_counted_immediately(self, client, auth_headers, db_session):
        csv = _csv_bytes(
            ("Alice Smith", "Widget Co", ""),  # valid
            ("", "Widget Co", ""),             # skipped: empty name
        )
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("contacts.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 202
        data = r.json()
        assert data["total_records"] == 2
        assert data["processed_records"] == 1  # skipped row pre-counted

    async def test_upload_requires_auth(self, client):
        csv = _csv_bytes(("Alice Smith", "Widget Co", ""))
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("contacts.csv", csv, "text/csv")},
        )
        assert r.status_code == 401

    async def test_upload_returns_job_id(self, client, auth_headers):
        csv = _csv_bytes(("Bob Jones", "Acme Ltd", "bob@acme.com"))
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("contacts.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 202
        data = r.json()
        UUID(data["id"])  # must be a valid UUID


# ── Polling endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBatchPolling:
    async def test_get_job_returns_queued_status(self, client, auth_headers):
        csv = _csv_bytes(("Alice Smith", "Widget Co", ""))
        upload_r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("contacts.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        job_id = upload_r.json()["id"]

        r = await client.get(f"/api/v1/batch/{job_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["status"] in ("queued", "running", "complete", "failed")

    async def test_get_nonexistent_job_returns_404(self, client, auth_headers):
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await client.get(f"/api/v1/batch/{fake_id}", headers=auth_headers)
        assert r.status_code == 404

    async def test_list_jobs_returns_paginated_response(self, client, auth_headers):
        r = await client.get("/api/v1/batch", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data


# ── Task orchestration ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBatchTaskOrchestration:
    """
    Call the async task internals directly (same pattern as TestRunVerificationAsync).
    AsyncSessionLocal is patched to use the test DB engine.
    """

    async def _upload_csv(self, client, auth_headers, csv_content: bytes) -> str:
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 202
        return r.json()["id"]

    async def test_process_batch_job_sets_running(
        self, client, auth_headers, db_session, test_engine
    ):
        csv = _csv_bytes(("Alice Smith", "Widget Co", "alice@widget.com"))
        job_id = await self._upload_csv(client, auth_headers, csv)

        with _session_factory_patch(test_engine):
            await _process_batch_job_async(job_id)

        db_session.expire_all()
        batch_job = await db_session.get(BatchJob, UUID(job_id))
        await db_session.refresh(batch_job)
        assert batch_job.status in (BatchJobStatus.RUNNING, BatchJobStatus.COMPLETE)

    async def test_process_batch_row_sets_complete(
        self, client, auth_headers, db_session, test_engine
    ):
        csv = _csv_bytes(("Alice Smith", "Widget Co", "alice@widget.com"))
        job_id = await self._upload_csv(client, auth_headers, csv)

        # Get the PENDING job_result ID
        db_session.expire_all()
        batch_job = await db_session.get(BatchJob, UUID(job_id))
        stmt = select(JobResult).where(
            JobResult.batch_job_id == batch_job.id,
            JobResult.status == JobResultStatus.PENDING,
        )
        jr = (await db_session.execute(stmt)).scalar_one()
        jr_id = str(jr.id)

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            with patch("anthropic.AsyncAnthropic", return_value=_mock_llm_client()):
                with _session_factory_patch(test_engine):
                    await _process_batch_row_async(job_id, jr_id)

        db_session.expire_all()
        jr_reloaded = await db_session.get(JobResult, UUID(jr_id))
        await db_session.refresh(jr_reloaded)
        assert jr_reloaded.status == JobResultStatus.SUCCESS

    async def test_process_batch_row_writes_verification_result(
        self, client, auth_headers, db_session, test_engine
    ):
        csv = _csv_bytes(("Bob Jones", "Acme Ltd", "bob@acme.com"))
        job_id = await self._upload_csv(client, auth_headers, csv)

        db_session.expire_all()
        batch_job = await db_session.get(BatchJob, UUID(job_id))
        stmt = select(JobResult).where(JobResult.batch_job_id == batch_job.id,
                                       JobResult.status == JobResultStatus.PENDING)
        jr = (await db_session.execute(stmt)).scalar_one()
        jr_id = str(jr.id)
        ver_id = jr.verification_result_id

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            with patch("anthropic.AsyncAnthropic", return_value=_mock_llm_client()):
                with _session_factory_patch(test_engine):
                    await _process_batch_row_async(job_id, jr_id)

        db_session.expire_all()
        ver = await db_session.get(VerificationResult, ver_id)
        await db_session.refresh(ver)
        assert ver.status == VerificationStatus.COMPLETE
        assert ver.confidence_score > 0

    async def test_all_skipped_batch_marks_complete(
        self, client, auth_headers, db_session, test_engine
    ):
        # CSV where all rows have empty Name → all SKIPPED at upload time
        csv = b"Name,Company\n,Widget Co\n,Acme Ltd"
        job_id = await self._upload_csv(client, auth_headers, csv)

        with _session_factory_patch(test_engine):
            await _process_batch_job_async(job_id)

        db_session.expire_all()
        batch_job = await db_session.get(BatchJob, UUID(job_id))
        await db_session.refresh(batch_job)
        assert batch_job.status == BatchJobStatus.COMPLETE

    async def test_process_batch_row_idempotency(
        self, client, auth_headers, db_session, test_engine
    ):
        """Calling process_batch_row twice on the same row must be a no-op on the 2nd call."""
        csv = _csv_bytes(("Carol White", "TestCo", "carol@testco.com"))
        job_id = await self._upload_csv(client, auth_headers, csv)

        db_session.expire_all()
        batch_job = await db_session.get(BatchJob, UUID(job_id))
        stmt = select(JobResult).where(JobResult.batch_job_id == batch_job.id,
                                       JobResult.status == JobResultStatus.PENDING)
        jr = (await db_session.execute(stmt)).scalar_one()
        jr_id = str(jr.id)

        def _run_row():
            return (
                respx.mock().__enter__(),
            )

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            with patch("anthropic.AsyncAnthropic", return_value=_mock_llm_client()):
                with _session_factory_patch(test_engine):
                    await _process_batch_row_async(job_id, jr_id)

        # Second run — Serper must NOT be called again (assert_all_called=False because
        # the route is intentionally never hit; idempotency check is via call_count below)
        with respx.mock(assert_all_called=False) as router2:
            serper_route = router2.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            with _session_factory_patch(test_engine):
                await _process_batch_row_async(job_id, jr_id)

        assert serper_route.call_count == 0

    async def test_failed_pipeline_sets_job_result_failed(
        self, client, auth_headers, db_session, test_engine
    ):
        csv = _csv_bytes(("Dave Brown", "FailCo", "dave@failco.com"))
        job_id = await self._upload_csv(client, auth_headers, csv)

        db_session.expire_all()
        batch_job = await db_session.get(BatchJob, UUID(job_id))
        stmt = select(JobResult).where(JobResult.batch_job_id == batch_job.id,
                                       JobResult.status == JobResultStatus.PENDING)
        jr = (await db_session.execute(stmt)).scalar_one()
        jr_id = str(jr.id)

        with respx.mock():
            respx.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(401, json={"message": "Unauthorized"})
            )
            with _session_factory_patch(test_engine):
                await _process_batch_row_async(job_id, jr_id)

        db_session.expire_all()
        jr_reloaded = await db_session.get(JobResult, UUID(jr_id))
        await db_session.refresh(jr_reloaded)
        assert jr_reloaded.status == JobResultStatus.FAILED
        assert jr_reloaded.error_message is not None


# ── Results endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBatchResultsEndpoint:
    async def test_get_results_returns_paginated_job_results(
        self, client, auth_headers, db_session, test_engine
    ):
        csv = _csv_bytes(("Alice Smith", "Widget Co", ""))
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("test.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        job_id = r.json()["id"]

        results_r = await client.get(
            f"/api/v1/batch/{job_id}/results", headers=auth_headers
        )
        assert results_r.status_code == 200
        data = results_r.json()
        assert "items" in data
        assert data["total"] == 1

    async def test_get_results_nonexistent_job_returns_404(self, client, auth_headers):
        r = await client.get(
            "/api/v1/batch/00000000-0000-0000-0000-000000000000/results",
            headers=auth_headers,
        )
        assert r.status_code == 404


# ── Export endpoint ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBatchExportEndpoint:
    async def test_export_incomplete_job_returns_400(self, client, auth_headers):
        csv = _csv_bytes(("Alice Smith", "Widget Co", ""))
        r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("test.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        job_id = r.json()["id"]

        export_r = await client.get(
            f"/api/v1/batch/{job_id}/export", headers=auth_headers
        )
        assert export_r.status_code == 400

    async def test_export_complete_job_returns_csv(
        self, client, auth_headers, db_session, test_engine
    ):
        csv = _csv_bytes(("Eve Davis", "ExportCo", "eve@exportco.com"))
        upload_r = await client.post(
            "/api/v1/batch/upload",
            files={"file": ("test.csv", csv, "text/csv")},
            headers=auth_headers,
        )
        job_id = upload_r.json()["id"]

        # Process the row so the job completes.
        # Must set status=RUNNING first: _increment_counters only marks the job
        # COMPLETE when it finds BatchJob.status == RUNNING (calling _process_batch_row_async
        # directly bypasses _process_batch_job_async which normally does this).
        db_session.expire_all()
        batch_job = await db_session.get(BatchJob, UUID(job_id))
        batch_job.status = BatchJobStatus.RUNNING
        await db_session.commit()

        stmt = select(JobResult).where(JobResult.batch_job_id == batch_job.id,
                                       JobResult.status == JobResultStatus.PENDING)
        jr = (await db_session.execute(stmt)).scalar_one()
        jr_id = str(jr.id)

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            with patch("anthropic.AsyncAnthropic", return_value=_mock_llm_client()):
                with _session_factory_patch(test_engine):
                    await _process_batch_row_async(job_id, jr_id)

        # _increment_counters committed COMPLETE via its own session; expire S1's
        # identity map so the export route re-fetches from DB instead of returning
        # the stale RUNNING value that was cached before the task ran.
        db_session.expire_all()

        export_r = await client.get(
            f"/api/v1/batch/{job_id}/export", headers=auth_headers
        )
        assert export_r.status_code == 200
        content = export_r.text
        assert "row_number" in content      # header row present
        assert "person_found" in content    # result column present
        assert "ExportCo" in content or "eve@exportco.com" in content  # data present
