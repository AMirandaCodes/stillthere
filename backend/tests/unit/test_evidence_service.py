"""
Unit tests for EvidenceService.

All HTTP calls are intercepted by respx.  The service is instantiated with
an injected httpx.AsyncClient, matching how the pipeline creates it.
"""
import pytest
import respx
import httpx

from app.services.evidence_service import EvidenceService, PageContent, MAX_PAGES

_HTML = b"""
<html>
<head><title>John Smith - Director</title></head>
<body>
  <nav>Navigation</nav>
  <script>alert("evil")</script>
  <p>John Smith is the Director of Engineering at Acme Ltd.</p>
  <p>He joined the company in 2015.</p>
  <footer>Footer</footer>
</body>
</html>
"""

_URL = "https://example.com/profile"


# ── Fetching behaviour ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestFetchPages:
    async def test_returns_page_content_on_success(self):
        with respx.mock() as router:
            router.get(_URL).mock(
                return_value=httpx.Response(200, content=_HTML,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as client:
                svc = EvidenceService(http_client=client)
                pages = await svc.fetch_pages([_URL])

        assert len(pages) == 1
        assert pages[0].fetch_ok is True
        assert pages[0].title == "John Smith - Director"
        assert "John Smith" in pages[0].text

    async def test_strips_script_tags(self):
        with respx.mock() as router:
            router.get(_URL).mock(
                return_value=httpx.Response(200, content=_HTML,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as client:
                pages = await EvidenceService(http_client=client).fetch_pages([_URL])

        assert 'alert("evil")' not in pages[0].text

    async def test_strips_nav_and_footer(self):
        with respx.mock() as router:
            router.get(_URL).mock(
                return_value=httpx.Response(200, content=_HTML,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as client:
                pages = await EvidenceService(http_client=client).fetch_pages([_URL])

        assert "Navigation" not in pages[0].text
        assert "Footer" not in pages[0].text

    async def test_failed_request_returns_fetch_ok_false(self):
        with respx.mock() as router:
            router.get(_URL).mock(return_value=httpx.Response(500))
            async with httpx.AsyncClient() as client:
                pages = await EvidenceService(http_client=client).fetch_pages([_URL])

        assert pages[0].fetch_ok is False
        assert pages[0].error is not None

    async def test_non_html_content_type_returns_fetch_ok_false(self):
        url = "https://example.com/api/feed"  # no binary extension — request will be made
        with respx.mock() as router:
            router.get(url).mock(
                return_value=httpx.Response(200, content=b'{"k":"v"}',
                                            headers={"content-type": "application/json"})
            )
            async with httpx.AsyncClient() as client:
                pages = await EvidenceService(http_client=client).fetch_pages([url])

        assert pages[0].fetch_ok is False

    async def test_binary_extension_skipped_without_request(self):
        """PDF URLs must not even make an HTTP request."""
        with respx.mock(assert_all_called=False) as router:
            router.get("https://example.com/report.pdf").mock(
                return_value=httpx.Response(200, content=b"%PDF")
            )
            async with httpx.AsyncClient() as client:
                pages = await EvidenceService(http_client=client).fetch_pages(
                    ["https://example.com/report.pdf"]
                )

        assert pages[0].fetch_ok is False
        assert len(router.calls) == 0  # no HTTP request made

    async def test_respects_max_pages_limit(self):
        urls = [f"https://example.com/page/{i}" for i in range(20)]
        with respx.mock() as router:
            router.get(url__regex=r"https://example\.com/page/\d+").mock(
                return_value=httpx.Response(200, content=_HTML,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as client:
                pages = await EvidenceService(http_client=client).fetch_pages(
                    urls, max_pages=3
                )

        assert len(pages) == 3

    async def test_text_truncated_to_max_chars(self):
        long_body = b"<html><body><p>" + b"x" * 10_000 + b"</p></body></html>"
        with respx.mock() as router:
            router.get(_URL).mock(
                return_value=httpx.Response(200, content=long_body,
                                            headers={"content-type": "text/html"})
            )
            async with httpx.AsyncClient() as client:
                pages = await EvidenceService(http_client=client).fetch_pages([_URL])

        assert len(pages[0].text) <= 5_000


# ── Static helpers ─────────────────────────────────────────────────────────────

class TestPrioritise:
    def test_linkedin_profile_ranked_first(self):
        urls = [
            "https://example.com/about",
            "https://linkedin.com/in/john-smith",
            "https://companies-house.gov.uk/company/123",
        ]
        ordered = EvidenceService._prioritise(urls)
        assert ordered[0] == "https://linkedin.com/in/john-smith"

    def test_social_media_ranked_last(self):
        urls = [
            "https://facebook.com/johndoe",
            "https://acme-corp.com/team",
        ]
        ordered = EvidenceService._prioritise(urls)
        assert ordered[0] == "https://acme-corp.com/team"

    def test_deduplicates_urls(self):
        urls = ["https://example.com", "https://example.com", "https://other.com"]
        assert len(EvidenceService._prioritise(urls)) == 2

    def test_preserves_all_unique_urls(self):
        urls = ["https://a.com", "https://b.com", "https://c.com"]
        assert len(EvidenceService._prioritise(urls)) == 3


class TestShouldSkip:
    def test_pdf_skipped(self):
        assert EvidenceService._should_skip("https://example.com/doc.pdf") is True

    def test_image_skipped(self):
        assert EvidenceService._should_skip("https://example.com/pic.jpg") is True

    def test_javascript_skipped(self):
        assert EvidenceService._should_skip("https://example.com/bundle.js") is True

    def test_html_page_not_skipped(self):
        assert EvidenceService._should_skip("https://example.com/about") is False

    def test_query_params_stripped_before_check(self):
        assert EvidenceService._should_skip("https://example.com/file.pdf?v=2") is True

    def test_path_without_extension_not_skipped(self):
        assert EvidenceService._should_skip("https://example.com/team/john") is False
