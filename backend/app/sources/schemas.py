from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar, Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, AfterValidator

from app.core.enums import SourceStatus
from app.core.utils import validate_lat, validate_lon

from app.iot.schemas import IoTDeviceOut

T: TypeVar = TypeVar("T")

type Latitude = Annotated[
    Decimal,
    Field(
        ge=Decimal("-90"),
        le=Decimal("90"),
        max_digits=9,
        decimal_places=6,
        description="Latitude in decimal degrees (-90 to 90)",
        examples=[Decimal("49.993500")],
    ),
    AfterValidator(validate_lat)
]

type Longitude = Annotated[
    Decimal,
    Field(
        ge=Decimal("-180"),
        le=Decimal("180"),
        max_digits=9,
        decimal_places=6,
        description="Longitude in decimal degrees (-180 to 180)",
        examples=[Decimal("-90.0000")],
    ),
    AfterValidator(validate_lon)
]


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Standard paginated response wrapper matching `X-Total-Count` header.
    """

    items: list[T] = Field(
        ..., description="Page items."
    )
    total: int = Field(
        ...,
        ge=0,
        description="Total number of matching records."
    )
    skip: int = Field(
        ...,
        ge=0,
        description="Number of skipped records."
    )
    limit: int = Field(
        ...,
        ge=1,
        description="Maximum number of returned records."
    )


class SourceCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Human-readable name of the solar or wind generation station.",
        examples=["Kharkiv Solar Array #1"],
    )
    # noinspection SpellCheckingInspection
    source_address: str | None = Field(
        default=None,
        max_length=255,
        description="Physical street address or site location description.",
        examples=["Kharkiv Oblast, Saltivskyi District, Sector 4"],
    )
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    qr_code: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Unique QR code payload printed on the station nameplate.",
        examples=["WM-SRC-2026-0001"],
    )
    source_status: SourceStatus = Field(
        default=SourceStatus.ACTIVE,
        description="Operational status of the generation facility.",
        examples=[SourceStatus.ACTIVE],
    )
    owner_user_id: UUID | None = Field(
        default=None,
        description=(
            "Optional owner user UUID (usable by ROOT/ADMIN when provisioning on behalf "
            "of a client; non-admin creators are automatically assigned as OWNER)."
        ),
    )


class SourceUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Updated energy source name.",
        examples=["Kharkiv Solar Array #1 (Upgraded)"],
    )
    source_address: str | None = Field(
        default=None,
        max_length=255,
        description="Updated physical address.",
    )
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    qr_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=255,
        description="Updated unique QR code identifier.",
    )
    source_status: SourceStatus | None = Field(
        default=None,
        description=(
            "Updated operational status (`active`, `inactive`, `maintenance`, `offline`)."
        ),
    )


class SourceShortOut(BaseModel):
    """
    Lightweight projection of an energy source for paginated list views.
    """

    model_config = ConfigDict(from_attributes=True)

    source_id: UUID = Field(
        ..., description="Unique UUID of the energy source."
    )
    source_name: str = Field(
        ..., description="Name of the energy source."
    )
    source_address: str | None = Field(
        default=None, description="Site physical address."
    )
    latitude: Decimal | None = Field(
        default=None, description="Latitude in degrees."
    )
    longitude: Decimal | None = Field(
        default=None, description="Longitude in degrees."
    )
    qr_code: str = Field(
        ..., description="Unique QR code identifier for mobile scanning."
    )
    source_status: SourceStatus = Field(
        ..., description="Current operational status."
    )
    is_active: bool = Field(
        ..., description="Whether the source is active (not archived)."
    )
    created_at: datetime = Field(
        ..., description="UTC timestamp of source creation."
    )


class SourceDetailOut(SourceShortOut):
    """
    Detailed energy source representation including attached ESP32 IoT devices.
    """

    iot_devices: list[IoTDeviceOut] = Field(
        default_factory=list,
        description="List of IoT microcontrollers commissioned on this source.",
    )
