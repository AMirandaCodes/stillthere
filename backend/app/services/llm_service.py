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
"""
import json
import re
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError, field_validator

from app.core.logging import get_logger
from app.models.enums import EvidenceSourceType, TriState
from app.services.evidence_service import PageContent
from app.services.search_service import SearchResults

logger = get_logger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1_024

_SYSTEM_PROMPT = """\
You are an evidence analyst for a contact verification system.

RULES — follow exactly:
1. Base your answers ONLY on the evidence provided to you. Do not use prior knowledge.
2. If evidence does not directly and clearly support YES or NO, you MUST return "unclear".
3. "unclear" is never wrong — it is the correct answer when evidence is insufficient.
4. Only cite URLs that appear in the evidence you were given.
5. Return ONLY valid JSON — no preamble, no markdown fences, no text outside the JSON object.
"""


# ── Output schema ──────────────────────────────────────────────────────────────

class LLMEvidenceSource(BaseModel):
    url: str = ""
    title: str = ""
    source_type: EvidenceSourceType = EvidenceSourceType.SEARCH_RESULT
    explanation: str = ""

    @field_validator("source_type", mode="before")
    @classmethod
    def coerce_source_type(cls, v: Any) -> str:
        valid = {e.value for e in EvidenceSourceType}
        if isinstance(v, str) and v in valid:
            return v
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
    def filter_invalid_urls(cls, v: Any) -> dict[str, str]:
        if not isinstance(v, dict):
            return {}
        return {k: url for k, url in v.items() if isinstance(url, str) and url.startswith(("http://", "https://"))}

    @field_validator(
        "person_found", "appears_associated", "found_on_website",
        "company_active", "email_match",
        mode="before",
    )
    @classmethod
    def coerce_tristate(cls, v: Any) -> str:
        if isinstance(v, str) and v.lower() in {e.value for e in TriState}:
            return v.lower()
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
        Never raises — API failures and parse errors return all-unclear defaults.
        """
        prompt = self.build_prompt(name, company, email, search_results, pages)
        raw = ""
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
        except Exception as exc:
            logger.error("LLM API call failed", error=str(exc))
            return LLMAnalysisResult(raw_response=f"API error: {exc}")

        return self._parse_response(raw)

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
        ]

        for hit in search_results.hits[:15]:
            lines += [
                f"[Search result — {hit.query_type}]",
                f"Title: {hit.title}",
                f"URL: {hit.url}",
                f"Snippet: {hit.snippet}",
                "",
            ]

        for page in pages:
            if page.fetch_ok and page.text:
                lines += [
                    f"[Page content from {page.url}]",
                    f"Title: {page.title}",
                    page.text[:3_000],
                    "",
                ]

        schema_example = {
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
                "LinkedIn Profile": "url if found in evidence",
                "Company Website": "url if found in evidence",
            },
            "reasoning": "2–3 sentences summarising the evidence and your conclusions",
        }

        lines += [
            "== TASK ==",
            "",
            "Based ONLY on the evidence above, return a JSON object with this exact structure:",
            json.dumps(schema_example, indent=2),
            "",
            "Field definitions:",
            "  person_found        — evidence that this named person exists as a real individual",
            "  appears_associated  — evidence this person works / worked at the named company",
            "  found_on_website    — this person appears on the company's own website",
            "  company_active      — the company appears to be currently trading",
            "  email_match         — the provided email is linked to this person or company",
            "",
            "If evidence is absent or ambiguous for any field, you MUST use 'unclear'.",
            "Return ONLY the JSON object — nothing else.",
        ]

        return "\n".join(lines)

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
