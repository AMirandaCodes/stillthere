"""
Application-wide enum definitions.

Using Python's str+Enum so values are JSON-serialisable, Pydantic-compatible,
and usable directly as SQLAlchemy column values.

native_enum=False is used throughout: SQLAlchemy stores these as VARCHAR + CHECK
CONSTRAINT rather than PostgreSQL ENUM types.  This avoids the ALTER TYPE
complexity when adding new values during later migrations.
"""
from enum import StrEnum


class TriState(StrEnum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class SearchSource(StrEnum):
    SINGLE = "single"
    BATCH = "batch"


class EvidenceSourceType(StrEnum):
    SEARCH_RESULT = "search_result"
    COMPANY_WEBSITE = "company_website"
    PROFESSIONAL_PROFILE = "professional_profile"
    BUSINESS_DIRECTORY = "business_directory"
    OTHER = "other"


class BatchJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class JobResultStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
