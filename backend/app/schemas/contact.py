from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.verification import VerificationSummary


class ContactResponse(BaseModel):
    id: UUID
    full_name: str
    email: str | None
    created_at: datetime
    recent_verifications: list[VerificationSummary] = []

    model_config = {"from_attributes": True}


class ContactSummaryResponse(BaseModel):
    id: UUID
    full_name: str
    email: str | None
    total_verifications: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}
