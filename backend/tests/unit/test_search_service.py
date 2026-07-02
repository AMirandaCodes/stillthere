"""
Unit tests for SearchService.

All HTTP calls are intercepted by respx — no real network access.
The service is instantiated directly with an injected httpx.AsyncClient,
matching exactly how the task uses it.
"""
import pytest
import respx
import httpx

from app.services.search_service import SearchService, SearchResults, SearchHit, SERPER_ENDPOINT

_HIT = {
    "title": "John Smith — Director at Acme Ltd",
    "link": "https://linkedin.com/in/john-smith",
    "snippet": "John Smith is Director at Acme Ltd.",
}
_SERPER_OK = {"organic": [_HIT]}
_SERPER_EMPTY = {"organic": []}


# ── Query count ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestQueryCount:
    async def test_fires_three_queries_without_email(self):
        with respx.mock() as router:
            route = router.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=_SERPER_OK))
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="test-key", http_client=client)
                await svc.search("John Smith", "Acme Ltd")
        assert route.call_count == 3

    async def test_fires_four_queries_with_email(self):
        with respx.mock() as router:
            route = router.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=_SERPER_OK))
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="test-key", http_client=client)
                await svc.search("John Smith", "Acme Ltd", email="john@acme.com")
        assert route.call_count == 4


# ── Result parsing ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestResultParsing:
    async def test_returns_hits_from_organic_results(self):
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=_SERPER_OK))
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="test-key", http_client=client)
                results = await svc.search("John Smith", "Acme Ltd")
        assert results.total_hits > 0
        assert any(h.url == "https://linkedin.com/in/john-smith" for h in results.hits)

    async def test_deduplicates_urls_across_queries(self):
        """Same URL from different queries should appear only once."""
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=_SERPER_OK))
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="test-key", http_client=client)
                results = await svc.search("John Smith", "Acme Ltd")
        urls = [h.url for h in results.hits]
        assert len(urls) == len(set(urls))

    async def test_records_queries_run(self):
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=_SERPER_OK))
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="test-key", http_client=client)
                results = await svc.search("John Smith", "Acme Ltd")
        assert len(results.queries_run) == 3

    async def test_empty_organic_results(self):
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=_SERPER_EMPTY))
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="test-key", http_client=client)
                results = await svc.search("Nobody", "Ghost Corp")
        assert results.total_hits == 0
        assert len(results.queries_run) == 3  # queries ran, just no results

    async def test_raw_data_stored_per_query_type(self):
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=_SERPER_OK))
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="test-key", http_client=client)
                results = await svc.search("John Smith", "Acme Ltd")
        assert "person_company" in results.raw_data
        assert "linkedin" in results.raw_data


# ── Error handling ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestErrorHandling:
    async def test_single_query_failure_is_non_fatal(self):
        """One failing query (e.g. 503) should not abort the whole search."""
        call_count = 0

        def alternate(request, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503, json={"error": "unavailable"})
            return httpx.Response(200, json=_SERPER_OK)

        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(side_effect=alternate)
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="test-key", http_client=client)
                results = await svc.search("John Smith", "Acme Ltd")
        # At least some queries succeeded
        assert results.queries_run

    async def test_all_queries_fail_returns_empty_queries_run(self):
        """When all queries fail, queries_run is empty (pipeline will raise)."""
        with respx.mock() as router:
            router.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
            async with httpx.AsyncClient() as client:
                svc = SearchService(api_key="bad-key", http_client=client)
                results = await svc.search("John Smith", "Acme Ltd")
        assert results.queries_run == []
        assert results.total_hits == 0


# ── Static helpers ─────────────────────────────────────────────────────────────

class TestBuildQueries:
    def test_three_queries_without_email(self):
        queries = SearchService._build_queries("Alice", "Widgets Ltd", None)
        assert len(queries) == 3
        types = {q[1] for q in queries}
        assert types == {"person_company", "linkedin", "company"}

    def test_four_queries_with_email(self):
        queries = SearchService._build_queries("Alice", "Widgets Ltd", "alice@widgets.com")
        assert len(queries) == 4
        types = {q[1] for q in queries}
        assert "email" in types

    def test_query_text_contains_quoted_name_and_company(self):
        queries = SearchService._build_queries("Bob Jones", "Acme Ltd", None)
        texts = [q[0] for q in queries]
        assert any('"Bob Jones"' in t and '"Acme Ltd"' in t for t in texts)

    def test_linkedin_query_uses_site_colon(self):
        queries = SearchService._build_queries("X", "Y", None)
        linkedin_q = next(t for t, typ in queries if typ == "linkedin")
        assert linkedin_q.startswith("site:linkedin.com")


class TestCacheKey:
    def test_same_input_same_key(self):
        assert SearchService.query_cache_key("q") == SearchService.query_cache_key("q")

    def test_different_input_different_key(self):
        assert SearchService.query_cache_key("a") != SearchService.query_cache_key("b")

    def test_key_has_correct_prefix_and_suffix(self):
        key = SearchService.query_cache_key("test query")
        assert key.startswith("stillthere:search:")
        assert key.endswith(":results")
