"""
ConfidenceService — transparent 0–100 confidence scoring.

Score measures how confident we are in the OVERALL assessment — not whether
the contact was verified positively.  A contact definitively NOT found can
score 90 if the evidence is strong and clear.

Scoring rubric:
  Field determination  (0–50)  10 pts per field with a yes/no answer (not unclear)
  Source quality       (0–50)  sum of per-source weights, capped at 50

  Source weights by type:
    professional_profile  12   (LinkedIn, Xing, etc.)
    company_website       10
    business_directory     7
    search_result          5
    other                  3

Confidence level thresholds:
  HIGH   ≥ 70
  MEDIUM ≥ 40
  LOW    < 40
"""
from dataclasses import dataclass

from app.models.enums import ConfidenceLevel, EvidenceSourceType, TriState

_SOURCE_WEIGHTS: dict[EvidenceSourceType, int] = {
    EvidenceSourceType.PROFESSIONAL_PROFILE: 12,
    EvidenceSourceType.COMPANY_WEBSITE:       10,
    EvidenceSourceType.BUSINESS_DIRECTORY:     7,
    EvidenceSourceType.SEARCH_RESULT:          5,
    EvidenceSourceType.OTHER:                  3,
}

_LEVEL_THRESHOLDS: list[tuple[int, ConfidenceLevel]] = [
    (70, ConfidenceLevel.HIGH),
    (40, ConfidenceLevel.MEDIUM),
    (0,  ConfidenceLevel.LOW),
]


@dataclass
class ConfidenceResult:
    score: int
    level: ConfidenceLevel
    breakdown: dict[str, int]


class ConfidenceService:
    """
    Pure computation — no I/O, no external dependencies.
    Instantiate and call score() directly in tests without any mocking.
    """

    def score(
        self,
        tri_states: dict[str, TriState],
        source_types: list[EvidenceSourceType],
    ) -> ConfidenceResult:
        """
        Compute the confidence score and level.

        tri_states: mapping of field names to TriState values.
                    Expected keys: person_found, appears_associated,
                    found_on_website, company_active, email_match.
        source_types: list of EvidenceSourceType values from the LLM analysis.
        """
        field_score = self._field_score(tri_states)
        source_score = self._source_score(source_types)
        total = min(100, field_score + source_score)

        level = next(
            lvl for threshold, lvl in _LEVEL_THRESHOLDS if total >= threshold
        )

        return ConfidenceResult(
            score=total,
            level=level,
            breakdown={
                "field_determination": field_score,
                "source_quality": source_score,
                "total": total,
            },
        )

    @staticmethod
    def _field_score(tri_states: dict[str, TriState]) -> int:
        """10 points per field with a definite yes/no answer (max 50)."""
        determined = sum(1 for v in tri_states.values() if v != TriState.UNCLEAR)
        return determined * 10

    @staticmethod
    def _source_score(source_types: list[EvidenceSourceType]) -> int:
        """Sum source quality weights, capped at 50."""
        return min(50, sum(_SOURCE_WEIGHTS.get(t, 3) for t in source_types))
