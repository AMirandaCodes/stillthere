"""
Unit tests for ConfidenceService.

Pure computation — no mocking, no fixtures, no DB.
Instantiate ConfidenceService and call score() directly.
"""
import pytest

from app.models.enums import ConfidenceLevel, EvidenceSourceType, TriState
from app.services.confidence_service import ConfidenceService, ConfidenceResult

_ALL_UNCLEAR = {
    "person_found":       TriState.UNCLEAR,
    "appears_associated": TriState.UNCLEAR,
    "found_on_website":   TriState.UNCLEAR,
    "company_active":     TriState.UNCLEAR,
    "email_match":        TriState.UNCLEAR,
}

_ALL_YES = {k: TriState.YES for k in _ALL_UNCLEAR}
_ALL_NO  = {k: TriState.NO  for k in _ALL_UNCLEAR}


@pytest.fixture
def svc() -> ConfidenceService:
    return ConfidenceService()


# ── Score range and level thresholds ──────────────────────────────────────────

class TestScoreRanges:
    def test_all_unclear_no_sources_gives_zero(self, svc):
        result = svc.score(_ALL_UNCLEAR, [])
        assert result.score == 0

    def test_all_yes_no_sources_gives_50(self, svc):
        result = svc.score(_ALL_YES, [])
        assert result.score == 50

    def test_all_no_counts_as_determined(self, svc):
        result = svc.score(_ALL_NO, [])
        assert result.score == 50  # NO is still a determined answer

    def test_score_never_exceeds_100(self, svc):
        sources = [EvidenceSourceType.PROFESSIONAL_PROFILE] * 20
        result = svc.score(_ALL_YES, sources)
        assert result.score <= 100

    def test_score_never_below_zero(self, svc):
        result = svc.score(_ALL_UNCLEAR, [])
        assert result.score >= 0

    def test_all_yes_with_quality_sources_gives_high(self, svc):
        sources = [
            EvidenceSourceType.PROFESSIONAL_PROFILE,
            EvidenceSourceType.COMPANY_WEBSITE,
            EvidenceSourceType.BUSINESS_DIRECTORY,
        ]
        result = svc.score(_ALL_YES, sources)
        assert result.score >= 70
        assert result.level == ConfidenceLevel.HIGH


class TestLevelThresholds:
    def test_high_at_70_or_above(self, svc):
        sources = [
            EvidenceSourceType.PROFESSIONAL_PROFILE,
            EvidenceSourceType.COMPANY_WEBSITE,
            EvidenceSourceType.BUSINESS_DIRECTORY,
        ]
        result = svc.score(_ALL_YES, sources)
        assert result.level == ConfidenceLevel.HIGH

    def test_low_below_40(self, svc):
        result = svc.score(_ALL_UNCLEAR, [])
        assert result.level == ConfidenceLevel.LOW

    def test_medium_between_40_and_70(self, svc):
        # 3 YES fields = 30 field pts; 1 business_directory (7) + 1 search_result (5) = 12 source pts → total 42
        partial = {**_ALL_UNCLEAR, "person_found": TriState.YES, "appears_associated": TriState.YES, "company_active": TriState.YES}
        sources = [EvidenceSourceType.BUSINESS_DIRECTORY, EvidenceSourceType.SEARCH_RESULT]
        result = svc.score(partial, sources)
        assert 40 <= result.score < 70
        assert result.level == ConfidenceLevel.MEDIUM


# ── Breakdown transparency ─────────────────────────────────────────────────────

class TestBreakdown:
    def test_breakdown_contains_required_keys(self, svc):
        result = svc.score(_ALL_YES, [EvidenceSourceType.SEARCH_RESULT])
        assert "field_determination" in result.breakdown
        assert "source_quality" in result.breakdown
        assert "total" in result.breakdown

    def test_breakdown_sums_to_total(self, svc):
        sources = [EvidenceSourceType.PROFESSIONAL_PROFILE, EvidenceSourceType.SEARCH_RESULT]
        result = svc.score(_ALL_YES, sources)
        assert (
            result.breakdown["field_determination"] + result.breakdown["source_quality"]
            == result.breakdown["total"]
        )

    def test_total_in_breakdown_equals_score(self, svc):
        sources = [EvidenceSourceType.COMPANY_WEBSITE]
        result = svc.score(_ALL_YES, sources)
        assert result.breakdown["total"] == result.score


# ── Field determination scoring ───────────────────────────────────────────────

class TestFieldScore:
    def test_each_determined_field_adds_10(self, svc):
        one = {**_ALL_UNCLEAR, "person_found": TriState.YES}
        two = {**_ALL_UNCLEAR, "person_found": TriState.YES, "company_active": TriState.NO}
        r1 = svc.score(one, [])
        r2 = svc.score(two, [])
        assert r2.score - r1.score == 10

    def test_max_field_score_is_50(self, svc):
        result = svc.score(_ALL_YES, [])
        assert result.breakdown["field_determination"] == 50


# ── Source quality scoring ─────────────────────────────────────────────────────

class TestSourceScore:
    def test_professional_profile_highest_weight(self, svc):
        r_prof = svc.score(_ALL_UNCLEAR, [EvidenceSourceType.PROFESSIONAL_PROFILE])
        r_other = svc.score(_ALL_UNCLEAR, [EvidenceSourceType.OTHER])
        assert r_prof.score > r_other.score

    def test_source_score_capped_at_50(self, svc):
        sources = [EvidenceSourceType.PROFESSIONAL_PROFILE] * 10
        result = svc.score(_ALL_UNCLEAR, sources)
        assert result.breakdown["source_quality"] == 50

    def test_return_types(self, svc):
        result = svc.score(_ALL_UNCLEAR, [])
        assert isinstance(result, ConfidenceResult)
        assert isinstance(result.score, int)
        assert isinstance(result.level, ConfidenceLevel)
        assert isinstance(result.breakdown, dict)
