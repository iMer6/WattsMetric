from datetime import datetime
from typing import Any, Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, AfterValidator

from app.core.utils import normalise_mac_address


MacAddress: type[str] = Annotated[
    str,
    Field(
        ...,
        min_length=12,
        max_length=17,
        description="Unique EUI-48 MAC address of the ESP32 microcontroller.",
        examples=["24:6f:28:a1:b2:c3"],
    ),
    AfterValidator(normalise_mac_address),
]


class IoTDeviceBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    firmware_version: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="ESP32 firmware semantic version string.",
        examples=["1.4.2-esp32"],
    )
    sensor_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSONB hardware sensor configuration (e.g., INA3221, BH1750, DS18B20 "
            "calibration coefficients and sampling interval in seconds)."
        ),
        examples=[
            {
                "sampling_interval_seconds": 60,
                "sensors": ["INA3221", "BH1750", "DS18B20"],
                "voltage_calibration_factor": 1.002,
            }
        ],
    )


class IoTDeviceCreateRequest(IoTDeviceBase):
    esp32_mac: str = MacAddress
    is_active: bool = Field(
        default=True,
        description=(
            "Indicates whether the IoT node is active and allowed to stream telemetry."
        ),
    )


class IoTDeviceUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    firmware_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="Updated ESP32 firmware version.",
        examples=["1.5.0-esp32"],
    )
    sensor_config: dict[str, Any] | None = Field(
        default=None,
        description="Updated JSONB sensor configuration.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or deactivate the IoT device.",
    )


class IoTDeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    iot_id: UUID = Field(
        ..., description="Unique UUID of the IoT device."
    )
    source_id: UUID = Field(
        ..., description="Parent energy source UUID."
    )
    esp32_mac: str = Field(
        ...,
        description="Normalised EUI-48 MAC address of the ESP32.",
        examples=["24:6f:28:a1:b2:c3"],
    )
    firmware_version: str = Field(
        ...,
        description="Current firmware version installed on the microcontroller.",
        examples=["1.4.2-esp32"],
    )
    sensor_config: dict[str, Any] = Field(
        default_factory=dict,
        description="JSONB configuration of attached sensors.",
    )
    is_active: bool = Field(
        ..., description="Whether the IoT device is currently active."
    )
    last_seen_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the last received telemetry packet.",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the IoT device was commissioned.",
    )
