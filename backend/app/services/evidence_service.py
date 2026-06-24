"""
EvidenceService — async page fetcher and HTML-to-text extractor.

Fetches up to MAX_PAGES URLs concurrently, extracts clean readable text
via BeautifulSoup, and returns PageContent objects for the LLM phase.

Individual fetch failures are isolated: one failed URL never stops the rest.
Non-HTML content types and binary file extensions are skipped entirely.

URL priority order (highest first):
  0 — LinkedIn /in/ profiles
  1 — Other linkedin.com pages
  2 — Company / business domains
  3 — Social-media aggregators (Facebook, Twitter, etc.)
"""
import asyncio
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_TEXT_CHARS = 5_000
MAX_PAGES = 8

_SKIP_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".png", ".jpg", ".jpeg",
     ".gif", ".svg", ".css", ".js", ".xml", ".json"}
)
_SOCIAL_SKIP = frozenset({"facebook.com", "twitter.com", "instagram.com", "tiktok.com", "youtube.com"})


def _is_network_error(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError))


@dataclass
class PageContent:
    url: str
    title: str
    text: str
    fetch_ok: bool
    error: str | None = None


class EvidenceService:
    """
    Fetches pages and extracts clean text for the LLM analysis phase.

    Accepts an injected httpx.AsyncClient so the HTTP layer can be replaced
    with respx mocks during unit tests without real network calls.
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    async def fetch_pages(
        self,
        urls: list[str],
        max_pages: int = MAX_PAGES,
    ) -> list[PageContent]:
        """
        Fetch up to max_pages URLs concurrently. Returns one PageContent per URL,
        with fetch_ok=False for any that fail or are skipped.
        """
        prioritised = self._prioritise(urls)[:max_pages]
        results = await asyncio.gather(
            *[self._fetch_one(url) for url in prioritised],
            return_exceptions=False,
        )
        return list(results)

    async def _fetch_one(self, url: str) -> PageContent:
        """Fetch a single URL; always returns PageContent, never raises."""
        if self._should_skip(url):
            return PageContent(url=url, title="", text="", fetch_ok=False,
                               error="skipped: binary extension")

        try:
            html = await self._fetch_with_retry(url)
        except Exception as exc:
            logger.debug("Page fetch failed", url=url, error=str(exc))
            return PageContent(url=url, title="", text="", fetch_ok=False,
                               error=str(exc)[:200])

        title, text = self._extract_text(html)
        return PageContent(url=url, title=title, text=text[:MAX_TEXT_CHARS], fetch_ok=True)

    @retry(
        retry=retry_if_exception(_is_network_error),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    async def _fetch_with_retry(self, url: str) -> bytes:
        response = await self._client.get(
            url,
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CVP-Bot/1.0; +https://github.com)"},
            follow_redirects=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            raise ValueError(f"Non-HTML content-type: {content_type}")
        return response.content

    @staticmethod
    def _extract_text(html: bytes) -> tuple[str, str]:
        """Parse HTML with lxml, strip boilerplate, return (title, clean_text)."""
        soup = BeautifulSoup(html, "lxml")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                          "noscript", "form", "button"]):
            tag.decompose()

        title = soup.title.get_text(strip=True) if soup.title else ""
        body = soup.find("body") or soup
        raw = body.get_text(separator=" ", strip=True)
        clean = re.sub(r"\s{3,}", "  ", raw)
        return title, clean

    @staticmethod
    def _prioritise(urls: list[str]) -> list[str]:
        """Deduplicate and rank URLs so most informative sources are fetched first."""
        def rank(url: str) -> int:
            u = url.lower()
            if "linkedin.com/in/" in u:
                return 0
            if "linkedin.com" in u:
                return 1
            if any(domain in u for domain in _SOCIAL_SKIP):
                return 3
            return 2

        seen: set[str] = set()
        deduped = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        return sorted(deduped, key=rank)

    @staticmethod
    def _should_skip(url: str) -> bool:
        u = url.lower().split("?")[0]
        return any(u.endswith(ext) for ext in _SKIP_EXTENSIONS)
