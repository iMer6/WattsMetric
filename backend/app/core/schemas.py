from datetime import datetime
from typing import Annotated, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.core.utils import ensure_utc_datetime


T: TypeVar = TypeVar("T")

type UtcDatetime = Annotated[
    datetime,
    AfterValidator(ensure_utc_datetime),
]


class ORMBaseModel(BaseModel):
    """
    Base schema for response models mapped from SQLAlchemy ORM Objects.
    """
    model_config = ConfigDict(from_attributes=True)


class TrimmedBaseModel(BaseModel):
    """
    Base schema for incoming payloads with automatic whitespace stripping.
    """
    model_config = ConfigDict(str_strip_whitespace=True)


class PaginatedResponse[T](BaseModel):
    """
    Standard paginated response wrapper matching `X-Total-Count` header.
    """
    items: list[T] = Field(..., description="Page items.")
    total: int = Field(
        ..., ge=0,
        description="Total number of matching records.",
    )
    skip: int = Field(
        ..., ge=0,
        description="Number of skipped records.",
    )
    limit: int = Field(
        ..., ge=1,
        description="Maximum number of returning records.",
    )
