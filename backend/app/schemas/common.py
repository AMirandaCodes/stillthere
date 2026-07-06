"""
Shared Pydantic schemas — pagination wrappers, enums, common response shapes.
"""
import math
from typing import Generic, TypeVar

from pydantic import BaseModel

DataT = TypeVar("DataT")


class PaginatedResponse(BaseModel, Generic[DataT]):
    items: list[DataT]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def build(
        cls,
        items: list,
        total: int,
        offset: int,
        limit: int,
    ) -> "PaginatedResponse":
        return cls(
            items=items,
            total=total,
            page=(offset // limit) + 1 if limit else 1,
            page_size=limit,
            total_pages=math.ceil(total / limit) if total and limit else 0,
        )


class ErrorResponse(BaseModel):
    detail: str


class SuccessResponse(BaseModel):
    message: str
