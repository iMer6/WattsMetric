from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import SourceStatus
from app.iot.schemas import IoTDeviceOut

T: TypeVar = TypeVar("T")

MIN_LATITUDE: Decimal = Decimal("-90")
MAX_LATITUDE: Decimal = Decimal("90")
MIN_LONGITUDE: Decimal = Decimal("-180")
MAX_LONGITUDE: Decimal = Decimal("180")


def _validate_lat(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None

    is_out_of_bounds: bool = value < MIN_LATITUDE or value > MAX_LATITUDE
    if is_out_of_bounds:
        raise ValueError("Latitude must be between -90 and 90 degrees.")

    return value


def _validate_lon(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None

    is_out_of_bounds: bool = value < MIN_LONGITUDE or value > MAX_LONGITUDE
    if is_out_of_bounds:
        raise ValueError("Longitude must be between -180 and 180 degrees.")

    return value


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
    latitude: Decimal | None = Field(
        default=None,
        max_digits=9,
        decimal_places=6,
        description="Geographic latitude in decimal degrees (-90 to 90).",
        examples=[Decimal("49.993500")],
    )
    longitude: Decimal | None = Field(
        default=None,
        max_digits=9,
        decimal_places=6,
        description="Geographic longitude in decimal degrees (-180 to 180).",
        examples=[Decimal("36.230400")],
    )
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

    @field_validator("latitude")
    @classmethod
    def check_latitude(cls, latitude: Decimal | None) -> Decimal | None:
        return _validate_lat(latitude)

    @field_validator("longitude")
    @classmethod
    def check_longitude(cls, longitude: Decimal | None) -> Decimal | None:
        return _validate_lon(longitude)


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
    latitude: Decimal | None = Field(
        default=None,
        max_digits=9,
        decimal_places=6,
        description="Updated latitude in decimal degrees (-90 to 90).",
    )
    longitude: Decimal | None = Field(
        default=None,
        max_digits=9,
        decimal_places=6,
        description="Updated longitude in decimal degrees (-180 to 180).",
    )
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

    @field_validator("latitude")
    @classmethod
    def check_latitude(cls, latitude: Decimal | None) -> Decimal | None:
        return _validate_lat(latitude)

    @field_validator("longitude")
    @classmethod
    def check_longitude(cls, longitude: Decimal | None) -> Decimal | None:
        return _validate_lon(longitude)


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
