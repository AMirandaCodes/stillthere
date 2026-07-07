"""
Maps a PipelineResult onto a VerificationResult ORM object.

Extracted from pipeline.py (SP-02) so that ORM mutation logic has a dedicated
module separate from the pure async pipeline computation.
"""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import VerificationStatus
from app.models.evidence_source import EvidenceSource
from app.models.verification_result import VerificationResult
from app.tasks.pipeline import PipelineResult


def apply_pipeline_result(
    result: VerificationResult,
    pipeline_result: PipelineResult,
    session: AsyncSession,
    result_uuid: UUID,
) -> None:
    """Write all PipelineResult fields onto result and add EvidenceSource rows to session."""
    result.status = VerificationStatus.COMPLETE
    result.person_found = pipeline_result.person_found
    result.appears_associated = pipeline_result.appears_associated
    result.found_on_website = pipeline_result.found_on_website
    result.company_active = pipeline_result.company_active
    result.email_match = pipeline_result.email_match
    result.confidence_score = pipeline_result.confidence_score
    result.confidence_level = pipeline_result.confidence_level
    result.useful_links = pipeline_result.useful_links
    result.raw_search_data = pipeline_result.raw_search_data
    for src in pipeline_result.evidence_sources:
        session.add(
            EvidenceSource(
                verification_result_id=result_uuid,
                url=src.url,
                title=src.title or None,
                snippet=None,
                explanation=src.explanation or None,
                source_type=src.source_type,
            )
        )
