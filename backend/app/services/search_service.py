"""
SearchService — Serper.dev integration for the verification pipeline.

Fires up to 4 targeted queries per verification:
  1. "{name}" "{company}"                   — primary person + company
  2. site:linkedin.com "{name}" "{company}" — professional profile
  3. "{company}" company                    — company information
  4. "{email}"                              — email footprint (only when provided)

Individual query failures are non-fatal: logged and skipped so remaining
queries still run.  Only when ALL queries fail is queries_run empty, which
the pipeline treats as a configuration error (bad API key, etc.).

Retry policy (tenacity): 5xx / network errors are retried up to 3 times with
exponential back-off; 4xx errors (auth, rate limit) propagate immediately.
"""
import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.logging import get_logger

logger = get_logger(__name__)

SERPER_ENDPOINT = "https://google.serper.dev/search"


@runtime_checkable
class SearchProvider(Protocol):
    """
    Structural interface for search providers.

    SearchService implements this protocol.  Pass any object that satisfies
    this interface to execute_pipeline() — Serper can be swapped for another
    provider without touching business logic.
    """

    async def search(
        self,
        name: str,
        company: str,
        email: str | None = None,
    ) -> "SearchResults":
        ...


def _is_retriable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError))


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    query_type: str   # "person_company" | "linkedin" | "company" | "email"
    position: int


@dataclass
class SearchResults:
    hits: list[SearchHit] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def total_hits(self) -> int:
        return len(self.hits)


class SearchService:
    """
    Provider-agnostic search layer backed by Serper.dev.

    Accepts an injected httpx.AsyncClient so the HTTP layer can be replaced
    with respx mocks during unit tests without real network calls.
    """

    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient,
        max_results_per_query: int = 10,
    ) -> None:
        self._api_key = api_key
        self._client = http_client
        self._max_results = max_results_per_query

    async def search(
        self,
        name: str,
        company: str,
        email: str | None = None,
    ) -> SearchResults:
        """
        Run 3–4 Serper queries and return deduplicated, merged SearchResults.
        Individual query failures are logged and skipped (non-fatal).
        """
        queries = self._build_queries(name, company, email)
        combined = SearchResults()
        seen_urls: set[str] = set()

        for query_text, query_type in queries:
            try:
                raw = await self._fetch_query(query_text)
            except Exception as exc:
                logger.warning(
                    "Search query failed — skipping",
                    query=query_text,
                    query_type=query_type,
                    error=str(exc),
                )
                continue

            combined.queries_run.append(query_text)
            combined.raw_data[query_type] = raw

            for pos, item in enumerate(raw.get("organic", []), start=1):
                url = item.get("link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                combined.hits.append(
                    SearchHit(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("snippet", ""),
                        query_type=query_type,
                        position=pos,
                    )
                )

        logger.info(
            "Search complete",
            name=name,
            company=company,
            queries_run=len(combined.queries_run),
            total_hits=combined.total_hits,
        )
        return combined

    @staticmethod
    def _build_queries(
        name: str,
        company: str,
        email: str | None,
    ) -> list[tuple[str, str]]:
        """Return (query_text, query_type) pairs. Email query only when present."""
        queries: list[tuple[str, str]] = [
            (f'"{name}" "{company}"', "person_company"),
            (f'site:linkedin.com "{name}" "{company}"', "linkedin"),
            (f'"{company}" company', "company"),
        ]
        if email:
            queries.append((f'"{email}"', "email"))
        return queries

    @staticmethod
    def query_cache_key(query_text: str) -> str:
        """Deterministic Redis key for a raw query string."""
        digest = hashlib.sha256(query_text.encode()).hexdigest()[:32]
        return f"stillthere:search:{digest}:results"

    @retry(
        retry=retry_if_exception(_is_retriable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        reraise=True,
    )
    async def _fetch_query(self, query_text: str) -> dict[str, Any]:
        """POST one query to Serper. Retried on 5xx / network errors; 4xx propagates."""
        response = await self._client.post(
            SERPER_ENDPOINT,
            headers={
                "X-API-KEY": self._api_key,
                "Content-Type": "application/json",
            },
            json={"q": query_text, "num": self._max_results},
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()
