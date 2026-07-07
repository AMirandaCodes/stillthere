"""
Unit tests for app.tasks.result_mapper.apply_pipeline_result.

Uses MagicMock for the ORM result and session — no DB required.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, call
from uuid import uuid4

import pytest

from app.models.enums import ConfidenceLevel, EvidenceSourceType, TriState, VerificationStatus
from app.tasks.pipeline import EvidenceData, PipelineResult
from app.tasks.result_mapper import apply_pipeline_result


def _pipeline_result(
    *,
    person_found: TriState = TriState.YES,
    appears_associated: TriState = TriState.YES,
    found_on_website: TriState = TriState.UNCLEAR,
    company_active: TriState = TriState.YES,
    email_match: TriState = TriState.NO,
    confidence_score: int = 72,
    confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH,
    evidence_sources: list[EvidenceData] | None = None,
    useful_links: dict | None = None,
    raw_search_data: dict | None = None,
) -> PipelineResult:
    return PipelineResult(
        person_found=person_found,
        appears_associated=appears_associated,
        found_on_website=found_on_website,
        company_active=company_active,
        email_match=email_match,
        confidence_score=confidence_score,
        confidence_level=confidence_level,
        evidence_sources=evidence_sources or [],
        useful_links=useful_links or {},
        raw_search_data=raw_search_data or {},
    )


class TestApplyPipelineResult:
    def test_sets_status_complete(self):
        result = MagicMock()
        apply_pipeline_result(result, _pipeline_result(), MagicMock(), uuid4())
        assert result.status == VerificationStatus.COMPLETE

    def test_sets_tristate_fields(self):
        result = MagicMock()
        pr = _pipeline_result(
            person_found=TriState.YES,
            appears_associated=TriState.NO,
            found_on_website=TriState.UNCLEAR,
            company_active=TriState.YES,
            email_match=TriState.NO,
        )
        apply_pipeline_result(result, pr, MagicMock(), uuid4())
        assert result.person_found == TriState.YES
        assert result.appears_associated == TriState.NO
        assert result.found_on_website == TriState.UNCLEAR
        assert result.company_active == TriState.YES
        assert result.email_match == TriState.NO

    def test_sets_confidence_score_and_level(self):
        result = MagicMock()
        apply_pipeline_result(
            result,
            _pipeline_result(confidence_score=85, confidence_level=ConfidenceLevel.HIGH),
            MagicMock(),
            uuid4(),
        )
        assert result.confidence_score == 85
        assert result.confidence_level == ConfidenceLevel.HIGH

    def test_sets_useful_links(self):
        result = MagicMock()
        links = {"LinkedIn": "https://linkedin.com/in/alice"}
        apply_pipeline_result(result, _pipeline_result(useful_links=links), MagicMock(), uuid4())
        assert result.useful_links == links

    def test_sets_raw_search_data(self):
        result = MagicMock()
        raw = {"search_queries": ["query1"], "llm_raw_response": "{}"}
        apply_pipeline_result(result, _pipeline_result(raw_search_data=raw), MagicMock(), uuid4())
        assert result.raw_search_data == raw

    def test_no_evidence_sources_no_session_add(self):
        result = MagicMock()
        session = MagicMock()
        apply_pipeline_result(result, _pipeline_result(evidence_sources=[]), session, uuid4())
        session.add.assert_not_called()

    def test_adds_evidence_source_to_session(self):
        result = MagicMock()
        session = MagicMock()
        result_uuid = uuid4()

        src = EvidenceData(
            url="https://linkedin.com/in/alice",
            title="Alice Smith",
            source_type=EvidenceSourceType.PROFESSIONAL_PROFILE,
            explanation="Profile confirms employment",
        )
        apply_pipeline_result(result, _pipeline_result(evidence_sources=[src]), session, result_uuid)
        session.add.assert_called_once()

    def test_adds_multiple_evidence_sources(self):
        result = MagicMock()
        session = MagicMock()

        sources = [
            EvidenceData(
                url=f"https://example.com/{i}",
                title=f"Source {i}",
                source_type=EvidenceSourceType.SEARCH_RESULT,
                explanation=f"Explanation {i}",
            )
            for i in range(3)
        ]
        apply_pipeline_result(result, _pipeline_result(evidence_sources=sources), session, uuid4())
        assert session.add.call_count == 3

    def test_evidence_source_has_correct_url(self):
        from app.models.evidence_source import EvidenceSource

        result = MagicMock()
        session = MagicMock()
        result_uuid = uuid4()

        src = EvidenceData(
            url="https://linkedin.com/in/alice",
            title="Alice",
            source_type=EvidenceSourceType.PROFESSIONAL_PROFILE,
            explanation="Confirmed",
        )
        apply_pipeline_result(result, _pipeline_result(evidence_sources=[src]), session, result_uuid)

        added_obj = session.add.call_args[0][0]
        assert isinstance(added_obj, EvidenceSource)
        assert added_obj.url == "https://linkedin.com/in/alice"
        assert added_obj.verification_result_id == result_uuid
