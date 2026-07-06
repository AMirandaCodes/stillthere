"""
Pydantic schemas for the verification domain.

Naming convention:
  *Create  — request body for creating a resource
  *Response — full response shape returned to the client
  *Summary  — lightweight shape used in list endpoints

These schemas intentionally duplicate some model fields rather than
inheriting from ORM models, keeping the API contract decoupled from
the database schema.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.models.enums import TriState, ConfidenceLevel, VerificationStatus, EvidenceSourceType
from app.core.security import sanitise_name, sanitise_company, sanitise_email


# ── Requests ────────────────────────────────────────────────────────────────

class VerificationCreate(BaseModel):
    full_name: str
    company_name: str
    work_email: str | None = None

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = sanitise_name(v)
        if not v:
            raise ValueError("full_name must not be empty")
        return v

    @field_validator("company_name")
    @classmethod
    def clean_company(cls, v: str) -> str:
        v = sanitise_company(v)
        if not v:
            raise ValueError("company_name must not be empty")
        return v

    @field_validator("work_email")
    @classmethod
    def clean_email(cls, v: str | None) -> str | None:
        return sanitise_email(v) if v else None


# ── Nested response shapes ───────────────────────────────────────────────────

class EvidenceSourceResponse(BaseModel):
    id: UUID
    url: str
    title: str | None
    snippet: str | None
    explanation: str | None
    source_type: EvidenceSourceType
    collected_at: datetime

    model_config = {"from_attributes": True}


class ContactSummary(BaseModel):
    id: UUID
    full_name: str
    email: str | None

    model_config = {"from_attributes": True}


class CompanySummary(BaseModel):
    id: UUID
    name: str
    domain: str | None
    website: str | None

    model_config = {"from_attributes": True}


# ── Primary response shapes ──────────────────────────────────────────────────

class VerificationJobResponse(BaseModel):
    """Returned immediately after a verification is submitted."""
    search_id: UUID
    verification_id: UUID
    status: VerificationStatus
    message: str = "Verification queued — processing has started"


class VerificationResultResponse(BaseModel):
    """Full report, returned once processing is complete (or for any status)."""
    id: UUID
    search_id: UUID
    status: VerificationStatus

    # Contact and company from the parent search
    full_name: str
    company_name: str
    work_email: str | None

    # Report fields
    person_found: TriState
    appears_associated: TriState
    found_on_website: TriState
    company_active: TriState
    email_match: TriState
    confidence_score: int
    confidence_level: ConfidenceLevel

    evidence_sources: list[EvidenceSourceResponse] = []
    useful_links: dict = {}
    error_message: str | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VerificationSummary(BaseModel):
    """Lightweight shape for history list views."""
    id: UUID
    search_id: UUID
    status: VerificationStatus
    full_name: str
    company_name: str
    confidence_score: int
    confidence_level: ConfidenceLevel
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminVerificationSummary(BaseModel):
    """Lightweight summary for the admin all-users view — includes submitter info."""
    id: UUID
    search_id: UUID
    status: VerificationStatus
    full_name: str
    company_name: str
    work_email: str | None
    user_email: str | None   # None = guest / unauthenticated search
    confidence_score: int
    confidence_level: ConfidenceLevel
    created_at: datetime

    model_config = {"from_attributes": True}
