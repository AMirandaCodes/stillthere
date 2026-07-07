"""
LLMService — Claude evidence analyser for the verification pipeline.

Evidence-first rule: the system prompt explicitly instructs the model to
return "unclear" for any field where the provided evidence does not directly
support a conclusion.  The service never guesses, infers, or fabricates.

JSON parsing strategy (most to least permissive):
  1. Direct json.loads on the raw response text
  2. Extract a ```json ... ``` fenced block
  3. Extract the first { ... } block via regex
  4. Fall back to all-unclear defaults (raw_response stored for debugging)

Resilience:
  - Tenacity retry: up to 3 attempts on 429 and 5xx, exponential backoff 5–30 s
  - Circuit breaker: opens after 3 consecutive retriable failures; stays open 120 s
  - 4xx errors (auth, bad request) propagate immediately without retry or tripping
    the circuit — they indicate a caller/config problem, not service instability
"""
import json
import re
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError, field_validator
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.circuit_breakers import CircuitBreakerOpen, anthropic_breaker
from app.core.logging import get_logger
from app.models.enums import EvidenceSourceType, TriState
from app.services.evidence_service import PageContent
from app.services.search_service import SearchResults

logger = get_logger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1_024

_PROMPT_SCHEMA = {
    "person_found": "yes | no | unclear",
    "appears_associated": "yes | no | unclear",
    "found_on_website": "yes | no | unclear",
    "company_active": "yes | no | unclear",
    "email_match": "yes | no | unclear",
    "evidence_sources": [
        {
            "url": "url from the evidence above",
            "title": "page title",
            "source_type": "search_result | company_website | professional_profile | business_directory | other",
            "explanation": "one sentence: what this source shows about the contact",
        }
    ],
    "useful_links": {
        "LinkedIn Profile": "linkedin.com/in/... personal profile URL — omit if only a company page was found",
        "Company Website": "company's own website URL if found in evidence",
    },
    "reasoning": "2–3 sentences summarising the evidence and your conclusions",
}

_SYSTEM_PROMPT = """\
You are an evidence analyst for a contact verification system.

RULES — follow exactly:
1. Base your answers ONLY on the evidence provided to you. Do not use prior knowledge.
2. If evidence does not directly and clearly support YES or NO, you MUST return "unclear".
3. "unclear" is never wrong — it is the correct answer when evidence is insufficient.
4. Only cite URLs that appear in the evidence you were given.
5. Return ONLY valid JSON — no preamble, no markdown fences, no text outside the JSON object.
"""


def _is_retriable_llm(exc: BaseException) -> bool:
    """Tenacity predicate: retry 429 and 5xx; never retry 4xx or CircuitBreakerOpen."""
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError))


# ── Output schema ──────────────────────────────────────────────────────────────

class LLMEvidenceSource(BaseModel):
    url: str = ""
    title: str = ""
    source_type: EvidenceSourceType = EvidenceSourceType.SEARCH_RESULT
    explanation: str = ""

    @field_validator("source_type", mode="before")
    @classmethod
    def coerce_source_type(cls, value: Any) -> str:
        valid = {e.value for e in EvidenceSourceType}
        if isinstance(value, str) and value in valid:
            return value
        return EvidenceSourceType.OTHER.value


class LLMAnalysisResult(BaseModel):
    person_found: TriState = TriState.UNCLEAR
    appears_associated: TriState = TriState.UNCLEAR
    found_on_website: TriState = TriState.UNCLEAR
    company_active: TriState = TriState.UNCLEAR
    email_match: TriState = TriState.UNCLEAR
    evidence_sources: list[LLMEvidenceSource] = []
    useful_links: dict[str, str] = {}
    reasoning: str = ""
    raw_response: str = ""

    @field_validator("useful_links", mode="before")
    @classmethod
    def filter_invalid_urls(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        result = {}
        for k, url in value.items():
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            # LinkedIn URLs in useful_links must be personal /in/ profiles.
            # Company pages (linkedin.com/company/...) are not a valid profile link.
            if "linkedin.com" in url.lower() and "/in/" not in url.lower():
                continue
            result[k] = url
        return result

    @field_validator(
        "person_found", "appears_associated", "found_on_website",
        "company_active", "email_match",
        mode="before",
    )
    @classmethod
    def coerce_tristate(cls, value: Any) -> str:
        if isinstance(value, str) and value.lower() in {e.value for e in TriState}:
            return value.lower()
        return TriState.UNCLEAR.value


# ── Service ────────────────────────────────────────────────────────────────────

class LLMService:
    """
    Wraps the Anthropic API for evidence analysis.

    Pass a pre-built anthropic.AsyncAnthropic instance via `client` to inject
    a mock during unit tests without any real API calls.
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self._model = model
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)

    @retry(
        retry=retry_if_exception(_is_retriable_llm),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=30),
        reraise=True,
    )
    async def analyse(
        self,
        name: str,
        company: str,
        email: str | None,
        search_results: SearchResults,
        pages: list[PageContent],
    ) -> LLMAnalysisResult:
        """
        Send collected evidence to Claude and return a structured analysis.

        Retried up to 3 times on 429 / 5xx (exponential backoff 5–30 s).
        Circuit breaker opens after 3 consecutive retriable failures; calls
        fast-fail for 120 s with CircuitBreakerOpen, which is not retried.
        Returns all-unclear defaults only on JSON parse failure.
        """
        if anthropic_breaker.is_open():
            raise CircuitBreakerOpen(
                "Anthropic circuit breaker open — LLM temporarily unavailable"
            )

        prompt = self.build_prompt(name, company, email, search_results, pages)
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                timeout=30.0,
            )
            anthropic_breaker.record_success()
        except anthropic.APIStatusError as exc:
            if exc.status_code < 500:
                raise  # 4xx: caller/config error; don't trip the circuit
            anthropic_breaker.record_failure()
            logger.error("LLM API server error", status=exc.status_code, error=str(exc))
            raise
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            anthropic_breaker.record_failure()
            logger.error("LLM API connection/timeout error", error=str(exc))
            raise
        except Exception as exc:
            logger.error("LLM API call failed", error=str(exc))
            raise

        return self._parse_response(message.content[0].text)

    @staticmethod
    def build_prompt(
        name: str,
        company: str,
        email: str | None,
        search_results: SearchResults,
        pages: list[PageContent],
    ) -> str:
        """
        Build the analysis prompt from collected evidence.

        Separated from analyse() so it can be tested independently without
        making an API call — just check the string contains expected content.
        """
        lines: list[str] = [
            "Verify this contact:",
            f"  Name: {name}",
            f"  Company: {company}",
            f"  Email: {email or 'not provided'}",
            "",
            "== EVIDENCE ==",
            "",
            *LLMService._format_search_evidence(search_results),
            *LLMService._format_page_evidence(pages),
            "== TASK ==",
            "",
            "Based ONLY on the evidence above, return a JSON object with this exact structure:",
            json.dumps(_PROMPT_SCHEMA, indent=2),
            "",
            "Field definitions:",
            "  person_found        — evidence that this named person exists as a real individual",
            "  appears_associated  — evidence this person works / worked at the named company",
            "  found_on_website    — this person appears on the company's own website",
            "  company_active      — the company appears to be currently trading",
            "  email_match         — the provided email is linked to this person or company",
            "  useful_links        — only include URLs that appear in the evidence above;",
            "                        'LinkedIn Profile' MUST be a personal profile URL (linkedin.com/in/...);",
            "                        a company page (linkedin.com/company/...) must NEVER be used as the LinkedIn Profile;",
            "                        include the personal profile even if the person appears to have left the company",
            "",
            "If evidence is absent or ambiguous for any field, you MUST use 'unclear'.",
            "Return ONLY the JSON object — nothing else.",
        ]

        return "\n".join(lines)

    @staticmethod
    def _format_search_evidence(search_results: SearchResults) -> list[str]:
        lines: list[str] = []
        for hit in search_results.hits[:15]:
            lines += [
                f"[Search result — {hit.query_type}]",
                f"Title: {hit.title}",
                f"URL: {hit.url}",
                f"Snippet: {hit.snippet}",
                "",
            ]
        return lines

    @staticmethod
    def _format_page_evidence(pages: list[PageContent]) -> list[str]:
        lines: list[str] = []
        for page in pages:
            if page.fetch_ok and page.text:
                lines += [
                    f"[Page content from {page.url}]",
                    f"Title: {page.title}",
                    page.text[:3_000],
                    "",
                ]
        return lines

    @staticmethod
    def _parse_response(raw: str) -> LLMAnalysisResult:
        """
        Try progressively more lenient strategies to extract JSON from the
        LLM response.  Always returns a valid LLMAnalysisResult.
        """
        candidates = [raw.strip(), *_extract_json_blocks(raw)]

        for text in candidates:
            try:
                data = json.loads(text)
                result = LLMAnalysisResult.model_validate(data)
                result.raw_response = raw
                return result
            except (json.JSONDecodeError, ValidationError):
                continue

        logger.warning(
            "LLM response could not be parsed — using unclear defaults",
            raw_preview=raw[:200],
        )
        return LLMAnalysisResult(raw_response=raw)


def _extract_json_blocks(text: str) -> list[str]:
    """Extract candidate JSON strings using two strategies."""
    candidates: list[str] = []

    # Strategy 1: ```json ... ``` fenced block
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE):
        candidates.append(match.group(1).strip())

    # Strategy 2: first outermost { ... } block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        candidates.append(brace_match.group())

    return candidates
