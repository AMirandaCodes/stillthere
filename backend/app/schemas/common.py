"""
Shared Pydantic schemas — pagination wrappers, enums, common response shapes.
"""
from typing import Generic, TypeVar
from pydantic import BaseModel

DataT = TypeVar("DataT")


class PaginatedResponse(BaseModel, Generic[DataT]):
    items: list[DataT]
    total: int
    page: int
    page_size: int
    total_pages: int


class ErrorResponse(BaseModel):
    detail: str


class SuccessResponse(BaseModel):
    message: str
