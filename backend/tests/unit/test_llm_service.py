"""
Unit tests for LLMService.

The Anthropic client is replaced with AsyncMock instances — no real API calls.
LLMService accepts an injected `client` kwarg specifically for this purpose.

build_prompt() is tested independently (no mock needed — pure string operation).
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.llm_service import (
    LLMService,
    LLMAnalysisResult,
    LLMEvidenceSource,
    _extract_json_blocks,
)
from app.services.search_service import SearchResults, SearchHit
from app.services.evidence_service import PageContent
from app.models.enums import EvidenceSourceType, TriState

# ── Test data ──────────────────────────────────────────────────────────────────

_NAME = "John Smith"
_COMPANY = "Acme Ltd"
_EMAIL = "john@acme.com"

_SEARCH_RESULTS = SearchResults(
    hits=[
        SearchHit(
            title="John Smith LinkedIn",
            url="https://linkedin.com/in/john-smith",
            snippet="Director at Acme Ltd",
            query_type="linkedin",
            position=1,
        )
    ],
    queries_run=['"John Smith" "Acme Ltd"'],
)

_PAGES = [
    PageContent(
        url="https://acme.com/team",
        title="Our Team",
        text="John Smith, Director of Engineering",
        fetch_ok=True,
    )
]

_VALID_RESPONSE = {
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
            "explanation": "LinkedIn profile confirms role at Acme Ltd.",
        }
    ],
    "useful_links": {"LinkedIn Profile": "https://linkedin.com/in/john-smith"},
    "reasoning": "LinkedIn evidence directly confirms John Smith works at Acme.",
}


def _make_client(response_text: str) -> MagicMock:
    """Build an AsyncMock anthropic client that returns response_text."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=response_text)]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    return mock_client


# ── analyse() ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAnalyse:
    async def test_parses_valid_json_response(self):
        svc = LLMService(api_key="k", client=_make_client(json.dumps(_VALID_RESPONSE)))
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)

        assert result.person_found == TriState.YES
        assert result.appears_associated == TriState.YES
        assert result.company_active == TriState.YES
        assert result.found_on_website == TriState.UNCLEAR
        assert len(result.evidence_sources) == 1

    async def test_parses_fenced_json_block(self):
        fenced = "Here is the result:\n```json\n" + json.dumps(_VALID_RESPONSE) + "\n```"
        svc = LLMService(api_key="k", client=_make_client(fenced))
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)
        assert result.person_found == TriState.YES

    async def test_extracts_bare_json_block(self):
        """LLM sometimes wraps JSON in prose — extract the { ... } block."""
        prose = "Sure! " + json.dumps(_VALID_RESPONSE) + " That's my analysis."
        svc = LLMService(api_key="k", client=_make_client(prose))
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)
        assert result.person_found == TriState.YES

    async def test_completely_invalid_response_returns_unclear_defaults(self):
        svc = LLMService(api_key="k", client=_make_client("Sorry, I cannot help."))
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)
        for field_name in ("person_found", "appears_associated", "found_on_website",
                           "company_active", "email_match"):
            assert getattr(result, field_name) == TriState.UNCLEAR
        assert "Sorry" in result.raw_response

    async def test_api_error_returns_unclear_defaults(self):
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=Exception("connection refused")
        )
        svc = LLMService(api_key="k", client=mock_client)
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)
        assert result.person_found == TriState.UNCLEAR
        assert "API error" in result.raw_response

    async def test_partial_json_uses_field_defaults(self):
        partial = json.dumps({"person_found": "yes"})  # missing all other fields
        svc = LLMService(api_key="k", client=_make_client(partial))
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)
        assert result.person_found == TriState.YES
        assert result.appears_associated == TriState.UNCLEAR  # default

    async def test_invalid_tristate_value_coerced_to_unclear(self):
        bad = {**_VALID_RESPONSE, "person_found": "maybe"}
        svc = LLMService(api_key="k", client=_make_client(json.dumps(bad)))
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)
        assert result.person_found == TriState.UNCLEAR

    async def test_invalid_source_type_coerced_to_other(self):
        bad = {
            **_VALID_RESPONSE,
            "evidence_sources": [
                {**_VALID_RESPONSE["evidence_sources"][0], "source_type": "social_media"}
            ],
        }
        svc = LLMService(api_key="k", client=_make_client(json.dumps(bad)))
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)
        assert result.evidence_sources[0].source_type == EvidenceSourceType.OTHER

    async def test_raw_response_stored_on_result(self):
        raw = json.dumps(_VALID_RESPONSE)
        svc = LLMService(api_key="k", client=_make_client(raw))
        result = await svc.analyse(_NAME, _COMPANY, _EMAIL, _SEARCH_RESULTS, _PAGES)
        assert result.raw_response == raw


# ── build_prompt() ─────────────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_contains_name_company_email(self):
        prompt = LLMService.build_prompt(
            "Alice Brown", "Widgets Ltd", "alice@widgets.com",
            _SEARCH_RESULTS, _PAGES,
        )
        assert "Alice Brown" in prompt
        assert "Widgets Ltd" in prompt
        assert "alice@widgets.com" in prompt

    def test_says_not_provided_when_no_email(self):
        prompt = LLMService.build_prompt("X", "Y", None, _SEARCH_RESULTS, _PAGES)
        assert "not provided" in prompt

    def test_contains_evidence_header(self):
        prompt = LLMService.build_prompt("X", "Y", None, _SEARCH_RESULTS, _PAGES)
        assert "EVIDENCE" in prompt

    def test_includes_search_hit_urls(self):
        prompt = LLMService.build_prompt("X", "Y", None, _SEARCH_RESULTS, _PAGES)
        assert "linkedin.com/in/john-smith" in prompt

    def test_includes_page_content(self):
        prompt = LLMService.build_prompt("X", "Y", None, _SEARCH_RESULTS, _PAGES)
        assert "Director of Engineering" in prompt

    def test_contains_field_definitions(self):
        prompt = LLMService.build_prompt("X", "Y", None, SearchResults(), [])
        assert "person_found" in prompt
        assert "unclear" in prompt

    def test_schema_example_in_prompt(self):
        prompt = LLMService.build_prompt("X", "Y", None, SearchResults(), [])
        assert "evidence_sources" in prompt
        assert "useful_links" in prompt

    def test_empty_evidence_still_produces_valid_prompt(self):
        prompt = LLMService.build_prompt("X", "Y", None, SearchResults(), [])
        assert len(prompt) > 100


# ── _extract_json_blocks() ─────────────────────────────────────────────────────

class TestExtractJsonBlocks:
    def test_extracts_fenced_json_block(self):
        text = 'Sure! ```json\n{"key": "value"}\n``` Done.'
        blocks = _extract_json_blocks(text)
        assert any('"key"' in b for b in blocks)

    def test_extracts_bare_brace_block(self):
        text = 'Result: {"key": "value"} end.'
        blocks = _extract_json_blocks(text)
        assert any('"key"' in b for b in blocks)

    def test_empty_text_returns_empty_list(self):
        assert _extract_json_blocks("no json here") == []

    def test_multiple_fenced_blocks_all_extracted(self):
        text = "```json\n{\"a\": 1}\n```\n```json\n{\"b\": 2}\n```"
        blocks = _extract_json_blocks(text)
        assert len(blocks) >= 2
