"""
Unit tests for app.schemas.builders.

Pure function tests — no DB, no mocking beyond MagicMock ORM stubs.
Verifies that build_result_response and build_summary correctly map
VerificationResult ORM attributes to Pydantic response schemas.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.enums import ConfidenceLevel, TriState, VerificationStatus
from app.schemas.builders import build_result_response, build_summary
from app.schemas.verification import VerificationResultResponse, VerificationSummary


def _stub_result(
    *,
    status: VerificationStatus = VerificationStatus.COMPLETE,
    person_found: TriState = TriState.YES,
    appears_associated: TriState = TriState.YES,
    found_on_website: TriState = TriState.UNCLEAR,
    company_active: TriState = TriState.YES,
    email_match: TriState = TriState.NO,
    confidence_score: int = 72,
    confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH,
    full_name: str = "Alice Smith",
    company_name: str = "Acme Ltd",
    work_email: str | None = "alice@acme.com",
    evidence_sources: list | None = None,
) -> MagicMock:
    now = datetime.now(timezone.utc)
    result = MagicMock()
    result.id = uuid4()
    result.search_id = uuid4()
    result.status = status
    result.person_found = person_found
    result.appears_associated = appears_associated
    result.found_on_website = found_on_website
    result.company_active = company_active
    result.email_match = email_match
    result.confidence_score = confidence_score
    result.confidence_level = confidence_level
    result.useful_links = {"LinkedIn": "https://linkedin.com/in/alice"}
    result.raw_search_data = {}
    result.error_message = None
    result.created_at = now
    result.updated_at = now
    result.evidence_sources = evidence_sources or []

    result.search = MagicMock()
    result.search.contact = MagicMock()
    result.search.contact.full_name = full_name
    result.search.company = MagicMock()
    result.search.company.name = company_name
    result.search.submitted_email = work_email

    return result


# ── build_result_response ──────────────────────────────────────────────────────

class TestBuildResultResponse:
    def test_returns_correct_type(self):
        r = build_result_response(_stub_result())
        assert isinstance(r, VerificationResultResponse)

    def test_maps_contact_and_company(self):
        r = build_result_response(_stub_result(full_name="Bob Jones", company_name="Globex"))
        assert r.full_name == "Bob Jones"
        assert r.company_name == "Globex"

    def test_maps_work_email(self):
        r = build_result_response(_stub_result(work_email="bob@globex.com"))
        assert r.work_email == "bob@globex.com"

    def test_null_work_email_preserved(self):
        r = build_result_response(_stub_result(work_email=None))
        assert r.work_email is None

    def test_maps_all_tristate_fields(self):
        r = build_result_response(_stub_result(
            person_found=TriState.YES,
            appears_associated=TriState.NO,
            found_on_website=TriState.UNCLEAR,
            company_active=TriState.YES,
            email_match=TriState.NO,
        ))
        assert r.person_found == TriState.YES
        assert r.appears_associated == TriState.NO
        assert r.found_on_website == TriState.UNCLEAR
        assert r.company_active == TriState.YES
        assert r.email_match == TriState.NO

    def test_maps_confidence(self):
        r = build_result_response(_stub_result(confidence_score=85, confidence_level=ConfidenceLevel.HIGH))
        assert r.confidence_score == 85
        assert r.confidence_level == ConfidenceLevel.HIGH

    def test_maps_status(self):
        r = build_result_response(_stub_result(status=VerificationStatus.COMPLETE))
        assert r.status == VerificationStatus.COMPLETE

    def test_evidence_sources_empty_by_default(self):
        r = build_result_response(_stub_result(evidence_sources=[]))
        assert r.evidence_sources == []

    def test_evidence_sources_mapped(self):
        src = MagicMock()
        src.id = uuid4()
        src.url = "https://linkedin.com/in/alice"
        src.title = "Alice Smith"
        src.snippet = None
        src.explanation = "Profile confirms role"
        from app.models.enums import EvidenceSourceType
        src.source_type = EvidenceSourceType.PROFESSIONAL_PROFILE
        src.collected_at = datetime.now(timezone.utc)

        r = build_result_response(_stub_result(evidence_sources=[src]))
        assert len(r.evidence_sources) == 1
        assert r.evidence_sources[0].url == "https://linkedin.com/in/alice"

    def test_raises_when_search_is_none(self):
        result = _stub_result()
        result.search = None
        with pytest.raises(ValueError, match="without required relations"):
            build_result_response(result)

    def test_raises_when_contact_is_none(self):
        result = _stub_result()
        result.search.contact = None
        with pytest.raises(ValueError, match="without required relations"):
            build_result_response(result)


# ── build_summary ──────────────────────────────────────────────────────────────

class TestBuildSummary:
    def test_returns_correct_type(self):
        s = build_summary(_stub_result())
        assert isinstance(s, VerificationSummary)

    def test_maps_name_and_company(self):
        s = build_summary(_stub_result(full_name="Carol White", company_name="WidgetCo"))
        assert s.full_name == "Carol White"
        assert s.company_name == "WidgetCo"

    def test_maps_confidence(self):
        s = build_summary(_stub_result(confidence_score=42, confidence_level=ConfidenceLevel.MEDIUM))
        assert s.confidence_score == 42
        assert s.confidence_level == ConfidenceLevel.MEDIUM

    def test_maps_status(self):
        s = build_summary(_stub_result(status=VerificationStatus.PENDING))
        assert s.status == VerificationStatus.PENDING

    def test_summary_does_not_include_tristate_fields(self):
        s = build_summary(_stub_result())
        assert not hasattr(s, "person_found")
        assert not hasattr(s, "evidence_sources")

    def test_raises_when_search_is_none(self):
        result = _stub_result()
        result.search = None
        with pytest.raises(ValueError, match="without required relations"):
            build_summary(result)
