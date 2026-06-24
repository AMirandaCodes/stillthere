from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CompanyResponse(BaseModel):
    id: UUID
    name: str
    domain: str | None
    website: str | None
    total_verifications: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}
