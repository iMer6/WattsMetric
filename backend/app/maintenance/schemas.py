from datetime import datetime
from typing import Generic, TypeVar, Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    AwareDatetime,
    AfterValidator
)

from app.core.utils import ensure_utc_datetime

from app.users.schemas import UserShortOut


T: TypeVar = TypeVar("T")

UtcDatetime: type[AwareDatetime] = Annotated[
    AwareDatetime,
    AfterValidator(ensure_utc_datetime),
]


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Standard paginated response wrapper matching `X-Total-Count` header.
    """

    items: list[T] = Field(
        ..., description="Page items."
    )
    total: int = Field(
        ..., ge=0,
        description="Total number of matching records."
    )
    skip: int = Field(
        ..., ge=0,
        description="Number of skipped records."
    )
    limit: int = Field(
        ..., ge=1,
        description="Maximum number of returned records."
    )


class MaintenancePhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    photo_id: UUID = Field(
        ...,
        description="Unique UUID of the maintenance photo."
    )
    session_id: UUID = Field(
        ...,
        description="Parent maintenance session UUID."
    )
    photo_url: str = Field(
        ...,
        description="Relative URL path to the stored inspection photograph.",
        examples=[
            "/uploads/maintenance/550e8400-e29b-41d4-a716-446655440000/photo.jpg"
        ],
    )
    created_at: datetime = Field(
        ...,
        description=(
            "UTC timestamp when the photo was captured on the mobile device."
        )
    )
    uploaded_at: datetime = Field(
        ...,
        description="UTC timestamp when the photo was uploaded to the server."
    )


class MaintenanceSessionCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: UUID | None = Field(
        default=None,
        description=(
            "Optional client-generated UUID for idempotent offline synchronization from "
            "the Android field engineer application."
        ),
    )
    inspector_notes: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description=(
            "Field inspection findings, cleaning logs, or hardware diagnostics notes."
        ),
        examples=[
            "Cleaned dust accumulation on string B; verified INA219 calibration via BLE."
        ],
    )
    created_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "UTC timestamp when the inspection was recorded offline on site. "
            "Defaults to current server UTC time if omitted."
        ),
        examples=["2026-11-22T08:30:00Z"],
    )
    inspector_ids: list[UUID] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Optional list of participating engineer UUIDs. The authenticated engineer "
            "is always included automatically."
        ),
    )


class MaintenanceSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID = Field(
        ...,
        description="Unique UUID of the maintenance session."
    )
    source_id: UUID = Field(
        ..., description="Inspected energy source UUID."
    )
    inspector_notes: str = Field(
        ..., description="Engineer inspection report text."
    )
    is_synced: bool = Field(
        ...,
        description=(
            "Indicates whether the offline record has been synchronized."
        )
    )
    created_at: datetime = Field(
        ..., description="UTC timestamp when the inspection took place."
    )
    synced_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the record reached the server."
    )
    inspectors: list[UserShortOut] = Field(
        default_factory=list,
        description="Service engineers assigned to this maintenance session.",
    )
    photos: list[MaintenancePhotoOut] = Field(
        default_factory=list,
        description="Photographs attached during the field inspection.",
    )
