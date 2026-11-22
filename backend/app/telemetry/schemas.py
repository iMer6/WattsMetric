from datetime import datetime
from decimal import Decimal
from typing import Any, Annotated
from uuid import UUID

from pydantic import BaseModel, Field, model_validator, AfterValidator

from app.core.utils import ensure_utc_datetime
from app.core.schemas import ORMBaseModel, TrimmedBaseModel


MAX_TELEMETRY_BATCH_SIZE: int = 1000

type MeasuredAt = Annotated[
    datetime,
    Field(
        description=(
            "Exact UTC timestamp when the sensor reading was captured on the ESP32."
        ),
        examples=["2026-11-22T09:15:00Z"],
    ),
    AfterValidator(ensure_utc_datetime),
]


# Telemetry Measurements


class MeasurementCreateItem(BaseModel):
    """
    Single synchronous sensor sample collected by an ESP32 node.
    """
    measured_at: MeasuredAt
    voltage: Decimal = Field(
        ...,
        ge=Decimal("-9999.99"),
        le=Decimal("9999.99"),
        max_digits=6,
        decimal_places=2,
        description="Measured electrical voltage in Volts (V).",
        examples=[Decimal("24.35")],
    )
    current: Decimal = Field(
        ...,
        ge=Decimal("-9999.99"),
        le=Decimal("9999.99"),
        max_digits=6,
        decimal_places=2,
        description="Measured electrical current in Amperes (A).",
        examples=[Decimal("8.42")],
    )
    temperature: Decimal | None = Field(
        default=None,
        ge=Decimal("-100.00"),
        le=Decimal("250.00"),
        max_digits=5,
        decimal_places=2,
        description=(
            "Ambient or panel surface temperature in degrees Celsius (°C) from DS18B20."
        ),
        examples=[Decimal("28.50")],
    )
    illumination: int | None = Field(
        default=None,
        ge=0,
        le=200000,
        description="Solar irradiance / illuminance in Lux (lx) from BH1750.",
        examples=[64500],
    )
    is_gap: bool = Field(
        default=False,
        description=(
            "Gap flag indicating missing or interpolated samples after network recovery."
        ),
        examples=[False],
    )


class MeasurementBatchIngestRequest(BaseModel):
    """
    Batch payload uploaded from ESP32 FIFO flash buffer (up to 1000 samples).
    """
    measurements: list[MeasurementCreateItem] = Field(
        ...,
        min_length=1,
        max_length=MAX_TELEMETRY_BATCH_SIZE,
        description=(
            f"Ordered or unordered list of telemetry readings "
            f"(1 to {MAX_TELEMETRY_BATCH_SIZE} items per request)."
        ),
    )


class MeasurementBatchIngestResponse(BaseModel):
    iot_id: UUID = Field(
        ..., description="Target IoT device UUID."
    )
    received_count: int = Field(
        ..., ge=1,
        description="Number of measurement records in the incoming payload."
    )
    processed_count: int = Field(
        ..., ge=1,
        description=(
            "Number of unique timestamp records idempotently upserted into storage."
        ),
    )
    gap_count: int = Field(
        ..., ge=0,
        description=(
            "Number of records marked with `is_gap=True` in the processed batch."
        ),
    )
    latest_measured_at: datetime = Field(
        ...,
        description="Most recent `measured_at` UTC timestamp in the batch."
    )


class MeasurementOut(ORMBaseModel):
    iot_id: UUID = Field(
        ..., description="Source IoT device UUID."
    )
    measured_at: datetime = Field(
        ..., description="UTC timestamp of the measurement."
    )
    voltage: Decimal = Field(
        ...,
        description="Voltage in Volts (V).",
        examples=[Decimal("24.35")]
    )
    current: Decimal = Field(
        ...,
        description="Current in Amperes (A).",
        examples=[Decimal("8.42")]
    )
    temperature: Decimal | None = Field(
        default=None,
        description="Temperature in Celsius (°C).",
        examples=[Decimal("28.50")]
    )
    illumination: int | None = Field(
        default=None,
        description="Illuminance in Lux (lx).",
        examples=[64500]
    )
    is_gap: bool = Field(
        ..., description="Whether this point marks a telemetry gap."
    )


# Time Aggregates


class TimeAggregateOut(ORMBaseModel):
    iot_id: UUID = Field(
        ...,
        description="IoT device UUID for which the bucket is aggregated."
    )
    bucket: datetime = Field(
        ...,
        description="Start UTC timestamp of the aggregation time bucket."
    )
    total_energy: Decimal = Field(
        ...,
        description=(
            "Integrated electrical energy produced within the bucket in kilowatt-hours (kWh)."
        ),
        examples=[Decimal("1.845")],
    )
    avg_voltage: Decimal | None = Field(
        default=None,
        description="Average voltage across the time bucket in Volts (V).",
        examples=[Decimal("24.10")],
    )
    avg_current: Decimal | None = Field(
        default=None,
        description="Average current across the time bucket in Amperes (A).",
        examples=[Decimal("7.95")],
    )
    avg_temperature: Decimal | None = Field(
        default=None,
        description="Average temperature across the time bucket in Celsius (°C).",
        examples=[Decimal("26.40")],
    )
    avg_illumination: Decimal | None = Field(
        default=None,
        description="Average illuminance across the time bucket in Lux (lx).",
        examples=[Decimal("58200.00")],
    )


# Test Profiles


class TestProfileCreateRequest(TrimmedBaseModel):
    test_profile_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description=(
            "Unique descriptive name for the calibration/replay test profile."
        ),
        examples=["Cloudy-to-Sunny Transient Drop Profile #4"],
    )
    profile_payload: dict[str, Any] = Field(
        ...,
        description=(
            "JSONB time-series simulation parameters and synthetic/archived sensor curve "
            "used for algorithm replay and alert threshold calibration."
        ),
        examples=[
            {
                "duration_minutes": 60,
                "step_seconds": 60,
                "samples": [
                    {
                        "offset_sec": 0,
                        "voltage": 24.1,
                        "current": 8.2,
                        "temperature": 25.0,
                        "illumination": 70000,
                    }
                ],
            }
        ],
    )

    @model_validator(mode="after")
    def ensure_non_empty_payload(self) -> "TestProfileCreateRequest":
        if not self.profile_payload:
            raise ValueError("profile_payload must not be empty.")
        return self


class TestProfileShortOut(ORMBaseModel):
    """
    Lightweight projection of a test profile for paginated listing.
    """
    test_profile_id: UUID = Field(
        ..., description="Unique UUID of the test profile."
    )
    test_profile_name: str = Field(
        ..., description="Unique test profile name."
    )
    created_at: datetime = Field(
        ..., description="UTC timestamp of profile creation."
    )


class TestProfileDetailOut(TestProfileShortOut):
    """
    Full test profile including JSONB `profile_payload` for replay execution.
    """
    profile_payload: dict[str, Any] = Field(
        ...,
        description=(
            "Full JSONB payload for time-series replay and calibration."
        )
    )
