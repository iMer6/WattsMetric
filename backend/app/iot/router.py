from fastapi import APIRouter, Response, status

from app.iot import crud
from app.iot.dependencies import (
    DeviceManageableSource,
    ManageableSourceDevice,
    ReadableSourceDevice,
)
from app.iot.models import IoTDevice
from app.iot.schemas import (
    IoTDeviceCreateRequest,
    IoTDeviceOut,
    IoTDeviceUpdateRequest,
)

from app.sources.dependencies import ReadableSource

from app.users.dependencies import DbSession

router: APIRouter = APIRouter()


@router.get(
    "/{source_id}/devices",
    tags=["IoT Devices"],
    response_model=list[IoTDeviceOut],
    summary="List IoT devices installed at an energy source",
    description=(
        "Lists all ESP32 microcontrollers registered at the specified energy source along with "
        "their firmware versions, JSONB sensor configurations, and `last_seen_at` heartbeats.\n\n"
        "- **Access Control:** `ROOT`, `ADMIN`, or assigned `OWNER`, `ENGINEER`, `DISPATCHER`."
    ),
)
async def list_source_devices(
    db: DbSession,
    source: ReadableSource,
) -> list[IoTDeviceOut]:
    devices: list[IoTDevice] = await crud.get_iot_devices_for_source(
        db, source_id=source.source_id,
    )
    return [IoTDeviceOut.model_validate(d) for d in devices]


@router.post(
    "/{source_id}/devices",
    tags=["IoT Devices"],
    response_model=IoTDeviceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Commission a new ESP32 IoT node on an energy source",
    description=(
        "Registers a new ESP32 measurement node after BLE provisioning on site.\n\n"
        "- **Uniqueness:** Enforces system-wide uniqueness of `esp32_mac`.\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, or `OWNER`/`ENGINEER` assigned to this facility."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Conflict — an IoT device with this MAC address is already registered.",
        },
    },
)
async def commission_iot_device(
    db: DbSession,
    source: DeviceManageableSource,
    payload: IoTDeviceCreateRequest,
) -> IoTDeviceOut:
    device: IoTDevice = await crud.create_iot_device(
        db,
        source_id=source.source_id,
        esp32_mac=payload.esp32_mac,
        firmware_version=payload.firmware_version,
        sensor_config=payload.sensor_config,
        is_active=payload.is_active,
    )
    return IoTDeviceOut.model_validate(device)


@router.get(
    "/{source_id}/devices/{iot_id}",
    tags=["IoT Devices"],
    response_model=IoTDeviceOut,
    summary="Get details of a specific ESP32 IoT device",
    description=(
        "Returns hardware configuration, firmware version, and `last_seen_at` status for a "
        "specific IoT node belonging to `source_id`."
    ),
)
async def get_iot_device_detail(
    source_and_device: ReadableSourceDevice,
) -> IoTDeviceOut:
    _, device = source_and_device
    return IoTDeviceOut.model_validate(device)


@router.patch(
    "/{source_id}/devices/{iot_id}",
    tags=["IoT Devices"],
    response_model=IoTDeviceOut,
    summary="Update ESP32 sensor configuration, firmware, or active status",
    description=(
        "Updates `sensor_config` JSONB calibration settings, `firmware_version`, or `is_active` "
        "state for an IoT node strictly belonging to `source_id`.\n\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, or `OWNER`/`ENGINEER` assigned to this facility."
    ),
)
async def update_iot_device(
    db: DbSession,
    source_and_device: ManageableSourceDevice,
    payload: IoTDeviceUpdateRequest,
) -> IoTDeviceOut:
    source, device = source_and_device
    updated: IoTDevice = await crud.update_iot_device(
        db,
        source_id=source.source_id,
        iot_id=device.iot_id,
        firmware_version=payload.firmware_version,
        sensor_config=payload.sensor_config,
        is_active=payload.is_active,
    )
    return IoTDeviceOut.model_validate(updated)


@router.delete(
    "/{source_id}/devices/{iot_id}",
    tags=["IoT Devices"],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Decommission (delete) an ESP32 IoT node",
    description=(
        "Removes an IoT device record belonging to `source_id`.\n\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, or `OWNER`/`ENGINEER` assigned to this facility."
    ),
)
async def delete_iot_device(
    db: DbSession,
    source_and_device: ManageableSourceDevice,
) -> Response:
    source, device = source_and_device
    await crud.delete_iot_device(
        db,
        source_id=source.source_id,
        iot_id=device.iot_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
