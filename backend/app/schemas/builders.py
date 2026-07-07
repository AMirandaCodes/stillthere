"""
Response builder functions — convert VerificationResult ORM objects to Pydantic schemas.

Extracted from VerificationService (SP-04) so that response-format changes have a
dedicated module to change without touching the service's business logic.
"""
from app.models.verification_result import VerificationResult
from app.schemas.verification import (
    EvidenceSourceResponse,
    VerificationResultResponse,
    VerificationSummary,
)


def build_result_response(result: VerificationResult) -> VerificationResultResponse:
    """
    Map a fully-loaded VerificationResult ORM object to its API response schema.
    Requires result.search, result.search.contact, result.search.company, and
    result.evidence_sources to all be eagerly loaded before calling.
    """
    search = result.search
    if search is None or search.contact is None or search.company is None:
        raise ValueError(
            f"VerificationResult {result.id} passed to build_result_response "
            "without required relations loaded"
        )
    return VerificationResultResponse(
        id=result.id,
        search_id=result.search_id,
        status=result.status,
        full_name=search.contact.full_name,
        company_name=search.company.name,
        work_email=search.submitted_email,
        person_found=result.person_found,
        appears_associated=result.appears_associated,
        found_on_website=result.found_on_website,
        company_active=result.company_active,
        email_match=result.email_match,
        confidence_score=result.confidence_score,
        confidence_level=result.confidence_level,
        evidence_sources=[
            EvidenceSourceResponse(
                id=e.id,
                url=e.url,
                title=e.title,
                snippet=e.snippet,
                explanation=e.explanation,
                source_type=e.source_type,
                collected_at=e.collected_at,
            )
            for e in result.evidence_sources
        ],
        useful_links=result.useful_links or {},
        error_message=result.error_message,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


def build_summary(result: VerificationResult) -> VerificationSummary:
    """Lightweight summary for list views. Requires search→contact/company loaded."""
    search = result.search
    if search is None or search.contact is None or search.company is None:
        raise ValueError(
            f"VerificationResult {result.id} passed to build_summary "
            "without required relations loaded"
        )
    return VerificationSummary(
        id=result.id,
        search_id=result.search_id,
        status=result.status,
        full_name=search.contact.full_name,
        company_name=search.company.name,
        confidence_score=result.confidence_score,
        confidence_level=result.confidence_level,
        created_at=result.created_at,
    )
