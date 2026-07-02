"""
Integration tests for the verification pipeline.

Tests execute_pipeline() end-to-end with mocked external I/O:
  - Serper API calls intercepted by respx
  - Page fetches intercepted by respx
  - Anthropic client replaced with AsyncMock

No database is required — execute_pipeline() accepts injected services and
returns a plain PipelineResult dataclass.  This isolates the pipeline logic
from both external APIs and the Celery task wrapper, making these tests fast
and deterministic.

Separate tests cover the _run_verification_async DB integration:
  - Reads the search context from DB (via patched AsyncSessionLocal)
  - Writes results back to DB after pipeline completion
  - Idempotency guard (COMPLETE → skip, RUNNING → crash recovery)
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.models.enums import TriState, VerificationStatus, ConfidenceLevel
from app.models.evidence_source import EvidenceSource
from app.models.verification_result import VerificationResult
from app.services.confidence_service import ConfidenceService
from app.services.evidence_service import EvidenceService
from app.services.llm_service import LLMService
from app.services.search_service import SearchService, SERPER_ENDPOINT
from app.tasks.verification_tasks import execute_pipeline, _run_verification_async, _PipelineError
from httpx import AsyncClient

# ── Fixtures / shared data ─────────────────────────────────────────────────────

_SERPER_RESPONSE = {
    "organic": [
        {
            "title": "John Smith — Director at Acme Ltd",
            "link": "https://linkedin.com/in/john-smith",
            "snippet": "John Smith is Director at Acme Ltd.",
        }
    ]
}

_SAMPLE_HTML = (
    b"<html><head><title>John Smith</title></head>"
    b"<body><p>John Smith, Director at Acme Ltd.</p></body></html>"
)

_LLM_FULL_RESPONSE = {
    "person_found": "yes",
    "appears_associated": "yes",
    "found_on_website": "unclear",
    "company_active": "yes",
    "email_match": "unclear",
    "evidence_sources": [
        {
            "url": "https://linkedin.com/in/john-smith",
            "title": "LinkedIn Profile",
            "source_type": "professional_profile",
            "explanation": "LinkedIn confirms John Smith at Acme Ltd.",
        }
    ],
    "useful_links": {"LinkedIn Profile": "https://linkedin.com/in/john-smith"},
    "reasoning": "Evidence supports the association between John Smith and Acme Ltd.",
}


def _mock_llm_client(response_dict: dict | None = None) -> AsyncMock:
    data = response_dict or _LLM_FULL_RESPONSE
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(data))]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=msg)
    return client


# ── execute_pipeline() — pure pipeline tests ──────────────────────────────────

@pytest.mark.asyncio
class TestExecutePipeline:
    async def test_returns_correct_tristate_fields(self):
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as http:
                result = await execute_pipeline(
                    name="John Smith",
                    company="Acme Ltd",
                    email="john@acme.com",
                    search_service=SearchService(api_key="test", http_client=http),
                    evidence_service=EvidenceService(http_client=http),
                    llm_service=LLMService(api_key="test", client=_mock_llm_client()),
                    confidence_service=ConfidenceService(),
                )

        assert result.person_found == TriState.YES
        assert result.appears_associated == TriState.YES
        assert result.company_active == TriState.YES
        assert result.found_on_website == TriState.UNCLEAR

    async def test_confidence_score_is_positive(self):
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as http:
                result = await execute_pipeline(
                    name="John Smith",
                    company="Acme Ltd",
                    email=None,
                    search_service=SearchService(api_key="test", http_client=http),
                    evidence_service=EvidenceService(http_client=http),
                    llm_service=LLMService(api_key="test", client=_mock_llm_client()),
                    confidence_service=ConfidenceService(),
                )

        assert result.confidence_score > 0
        assert result.confidence_level in list(ConfidenceLevel)

    async def test_evidence_sources_populated(self):
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as http:
                result = await execute_pipeline(
                    name="John Smith",
                    company="Acme Ltd",
                    email=None,
                    search_service=SearchService(api_key="test", http_client=http),
                    evidence_service=EvidenceService(http_client=http),
                    llm_service=LLMService(api_key="test", client=_mock_llm_client()),
                    confidence_service=ConfidenceService(),
                )

        assert len(result.evidence_sources) >= 1
        assert result.evidence_sources[0].url == "https://linkedin.com/in/john-smith"

    async def test_raw_search_data_stored(self):
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as http:
                result = await execute_pipeline(
                    name="John Smith",
                    company="Acme Ltd",
                    email=None,
                    search_service=SearchService(api_key="test", http_client=http),
                    evidence_service=EvidenceService(http_client=http),
                    llm_service=LLMService(api_key="test", client=_mock_llm_client()),
                    confidence_service=ConfidenceService(),
                )

        assert "search_queries" in result.raw_search_data
        assert "serper_raw" in result.raw_search_data
        assert "llm_raw_response" in result.raw_search_data
        assert "confidence_breakdown" in result.raw_search_data

    async def test_raises_pipeline_error_when_all_queries_fail(self):
        """If all Serper queries fail, execute_pipeline raises _PipelineError."""
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(401, json={"message": "Unauthorized"})
            )
            async with httpx.AsyncClient() as http:
                with pytest.raises(_PipelineError, match="queries failed"):
                    await execute_pipeline(
                        name="John Smith",
                        company="Acme Ltd",
                        email=None,
                        search_service=SearchService(api_key="bad-key", http_client=http),
                        evidence_service=EvidenceService(http_client=http),
                        llm_service=LLMService(api_key="test", client=_mock_llm_client()),
                        confidence_service=ConfidenceService(),
                    )

    async def test_completes_with_no_pages_fetched(self):
        """Pipeline should complete even if all page fetches fail (all-unclear OK)."""
        all_unclear_response = {
            **_LLM_FULL_RESPONSE,
            "person_found": "unclear",
            "appears_associated": "unclear",
            "company_active": "unclear",
            "evidence_sources": [],
        }

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(503)
            )
            async with httpx.AsyncClient() as http:
                result = await execute_pipeline(
                    name="John Smith",
                    company="Acme Ltd",
                    email=None,
                    search_service=SearchService(api_key="test", http_client=http),
                    evidence_service=EvidenceService(http_client=http),
                    llm_service=LLMService(
                        api_key="test",
                        client=_mock_llm_client(all_unclear_response),
                    ),
                    confidence_service=ConfidenceService(),
                )

        assert result.person_found == TriState.UNCLEAR
        assert result.confidence_score == 0


# ── _run_verification_async() — DB integration ────────────────────────────────

@pytest.mark.asyncio
class TestRunVerificationAsync:
    """
    Tests the task's DB orchestration using the test database.

    AsyncSessionLocal is patched to use the test engine so all DB reads/writes
    go through the same PostgreSQL instance as the rest of the integration tests.
    """

    def _session_factory_patch(self, test_engine):
        """Return a context-manager patch for AsyncSessionLocal."""
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        return patch("app.tasks.verification_tasks.AsyncSessionLocal", factory)

    async def _create_pending_result(self, client: AsyncClient, auth_headers) -> str:
        r = await client.post(
            "/api/v1/verifications",
            json={
                "full_name": "Pipeline User",
                "company_name": "Pipeline Corp",
                "work_email": "pipeline@corp.com",
            },
            headers=auth_headers,
        )
        return r.json()["verification_id"]

    async def test_sets_status_complete_after_pipeline(
        self, client: AsyncClient, auth_headers, db_session, test_engine
    ):
        vid = await self._create_pending_result(client, auth_headers)

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            with patch("anthropic.AsyncAnthropic", return_value=_mock_llm_client()):
                with self._session_factory_patch(test_engine):
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: asyncio.run(_run_verification_async(vid))
                    )

        db_session.expire_all()
        from uuid import UUID
        result = await db_session.get(VerificationResult, UUID(vid))
        await db_session.refresh(result)
        assert result.status == VerificationStatus.COMPLETE

    async def test_writes_evidence_sources_to_db(
        self, client: AsyncClient, auth_headers, db_session, test_engine
    ):
        from uuid import UUID
        vid = await self._create_pending_result(client, auth_headers)

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            with patch("anthropic.AsyncAnthropic", return_value=_mock_llm_client()):
                with self._session_factory_patch(test_engine):
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: asyncio.run(_run_verification_async(vid))
                    )

        db_session.expire_all()
        stmt = (
            select(VerificationResult)
            .options(selectinload(VerificationResult.evidence_sources))
            .where(VerificationResult.id == UUID(vid))
        )
        loaded = (await db_session.execute(stmt)).scalar_one()
        assert len(loaded.evidence_sources) >= 1
        assert loaded.evidence_sources[0].source_type.value == "professional_profile"

    async def test_sets_status_failed_when_all_queries_fail(
        self, client: AsyncClient, auth_headers, db_session, test_engine
    ):
        from uuid import UUID
        vid = await self._create_pending_result(client, auth_headers)

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(401, json={"message": "Unauthorized"})
            )
            with patch("anthropic.AsyncAnthropic", return_value=_mock_llm_client()):
                with self._session_factory_patch(test_engine):
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: asyncio.run(_run_verification_async(vid))
                    )

        db_session.expire_all()
        result = await db_session.get(VerificationResult, UUID(vid))
        await db_session.refresh(result)
        assert result.status == VerificationStatus.FAILED
        assert result.error_message is not None

    async def test_idempotency_complete_result_not_rerun(
        self, client: AsyncClient, auth_headers, db_session, test_engine
    ):
        """Running _run_verification_async on a COMPLETE result must be a no-op."""
        vid = await self._create_pending_result(client, auth_headers)

        # First run: complete it
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            router.get(url__regex=r"https://").mock(
                return_value=httpx.Response(200, content=_SAMPLE_HTML,
                                            headers={"content-type": "text/html"})
            )
            with patch("anthropic.AsyncAnthropic", return_value=_mock_llm_client()):
                with self._session_factory_patch(test_engine):
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: asyncio.run(_run_verification_async(vid))
                    )

        # Second run: Serper should NOT be called again (assert_all_called=False because
        # the route is intentionally never hit; idempotency is verified by call_count)
        with respx.mock(assert_all_called=False) as router2:
            serper_route = router2.post(SERPER_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_SERPER_RESPONSE)
            )
            with self._session_factory_patch(test_engine):
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: asyncio.run(_run_verification_async(vid))
                )

        assert serper_route.call_count == 0
