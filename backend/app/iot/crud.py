from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.enums import RoleName
from app.core.utils import normalise_mac_address

from app.iot.exceptions import *
from app.iot.models import IoTDevice
from app.sources.crud import ensure_user_source_permission
from app.sources.exceptions import SourceNotFoundError
from app.sources.models import Source


async def create_iot_device(
    db: AsyncSession,
    *,
    actor_id: UUID,
    source_id: UUID,
    esp32_mac: str,
    firmware_version: str | None = None,
    sensor_config: dict[str, Any] | None = None,
    is_active: bool = True,
) -> IoTDevice:
    """
    Registers a new ESP32 measurement node and links it to a generation source.

    Verifies OWNER or ENGINEER permissions for this station.
    """
    norm_mac: str = normalise_mac_address(esp32_mac)

    source_exists_stmt = select(
        exists().where(Source.source_id == source_id)
    )
    source_exists: bool | None = await db.scalar(source_exists_stmt)
    if not source_exists:
        raise SourceNotFoundError(f"Source {source_id} not found.")

    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=source_id,
        allowed_source_roles=(
            RoleName.OWNER.value,
            RoleName.ENGINEER.value,
        ),
    )

    mac_exists_stmt = select(
        exists().where(IoTDevice.esp32_mac == norm_mac)
    )
    mac_exists: bool | None = await db.scalar(mac_exists_stmt)
    if mac_exists:
        raise IoTDeviceMacAlreadyExistsError(
            f"IoT device with MAC '{norm_mac}' is already registered."
        )

    device: IoTDevice = IoTDevice(
        source_id=source_id,
        esp32_mac=norm_mac,
        firmware_version=firmware_version.strip() if firmware_version else None,
        sensor_config=sensor_config,
        is_active=is_active,
    )

    try:
        db.add(device)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        is_duplicate_mac_error: bool = "esp32_mac" in str(exc.orig).lower()
        if is_duplicate_mac_error:
            raise IoTDeviceMacAlreadyExistsError(
                f"IoT device with MAC '{norm_mac}' is already registered."
            ) from exc
        raise FailedToCreateIoTDeviceError() from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToCreateIoTDeviceError() from exc

    return await get_iot_device_by_id(
        db,
        cast(UUID, device.iot_id),
        actor_id=actor_id,
        include_source=True,
    )


async def get_iot_device_by_id(
    db: AsyncSession,
    iot_id: UUID,
    *,
    actor_id: UUID,
    include_source: bool = False,
) -> IoTDevice:
    """
    Retrieves an IoT device by its UUID.
    """
    iot_device_stmt = (
        select(IoTDevice)
        .where(IoTDevice.iot_id == iot_id)
    )
    if include_source:
        iot_device_stmt = (
            iot_device_stmt.options(joinedload(IoTDevice.source))
        )

    device: IoTDevice | None = await db.scalar(iot_device_stmt)
    is_device_missing: bool = device is None
    if is_device_missing:
        raise IoTDeviceNotFoundError(f"IoT device {iot_id} not found.")

    device_source_id: UUID = cast(UUID, device.source_id)
    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=device_source_id,
    )

    return device


async def get_iot_device_by_mac(
    db: AsyncSession,
    esp32_mac: str,
    *,
    actor_id: UUID,
    include_source: bool = True,
) -> IoTDevice:
    """
    Finds an IoT node by its ESP32 hardware MAC address.

    For telemetry ingestion and BLE diagnostics.
    """
    norm_mac: str = normalise_mac_address(esp32_mac)
    iot_device_stmt = (
        select(IoTDevice)
        .where(IoTDevice.esp32_mac == norm_mac)
    )
    if include_source:
        iot_device_stmt = (
            iot_device_stmt.options(joinedload(IoTDevice.source))
        )

    device: IoTDevice | None = await db.scalar(iot_device_stmt)
    is_device_missing: bool = device is None
    if is_device_missing:
        raise IoTDeviceNotFoundError(
            f"IoT device with MAC '{norm_mac}' not found."
        )

    device_source_id: UUID = cast(UUID, device.source_id)
    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=device_source_id,
    )

    return device


async def get_iot_devices_by_source(
    db: AsyncSession,
    source_id: UUID,
    *,
    actor_id: UUID,
    is_active: bool | None = None,
) -> list[IoTDevice]:
    """
    Returns a list of all IoT devices linked to the specified generation source.
    """
    source_exists_stmt = select(
        exists().where(Source.source_id == source_id)
    )
    source_exists: bool | None = await db.scalar(source_exists_stmt)
    if not source_exists:
        raise SourceNotFoundError(f"Source {source_id} not found.")

    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=source_id,
    )

    filters: list = [IoTDevice.source_id == source_id]
    has_active_filter: bool = is_active is not None
    if has_active_filter:
        filters.append(IoTDevice.is_active.is_(is_active))

    iot_devices_stmt = (
        select(IoTDevice)
        .where(and_(*filters))
        .order_by(
            IoTDevice.created_at.asc(),
            IoTDevice.iot_id.asc(),
        )
    )
    result = await db.scalars(iot_devices_stmt)
    return list(result.all())


async def update_iot_device(
    db: AsyncSession,
    *,
    iot_id: UUID,
    actor_id: UUID,
    source_id: UUID | None = None,
    firmware_version: str | None = None,
    sensor_config: dict[str, Any] | None = None,
    is_active: bool | None = None,
) -> IoTDevice:
    """
    Updates the sensor configuration (JSONB), firmware version,
    active status, or station assignment.
    """
    iot_device_stmt = (
        select(IoTDevice)
        .where(IoTDevice.iot_id == iot_id)
        .with_for_update()
    )
    device: IoTDevice | None = await db.scalar(iot_device_stmt)
    is_device_missing: bool = device is None
    if is_device_missing:
        raise IoTDeviceNotFoundError(f"IoT device {iot_id} not found.")

    current_source_id: UUID = cast(UUID, device.source_id)
    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=current_source_id,
        allowed_source_roles=(
            RoleName.OWNER.value,
            RoleName.ENGINEER.value,
        ),
    )

    is_source_changed: bool = (
        source_id is not None
        and source_id != current_source_id
    )
    if source_id is not None and is_source_changed:
        target_source_exists_stmt = select(
            exists().where(Source.source_id == source_id)
        )
        target_source_exists: bool | None = (
            await db.scalar(target_source_exists_stmt)
        )
        if not target_source_exists:
            raise SourceNotFoundError(
                f"Target source {source_id} not found."
            )

        await ensure_user_source_permission(
            db,
            user_id=actor_id,
            source_id=source_id,
            allowed_source_roles=(
                RoleName.OWNER.value,
                RoleName.ENGINEER.value,
            ),
        )
        device.source_id = source_id

    has_firmware_update: bool = firmware_version is not None
    if has_firmware_update:
        device.firmware_version = firmware_version.strip() or None

    has_sensor_config_update: bool = sensor_config is not None
    if has_sensor_config_update:
        device.sensor_config = sensor_config

    has_active_status_update: bool = is_active is not None
    if has_active_status_update:
        device.is_active = is_active

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateIoTDeviceError() from exc

    return await get_iot_device_by_id(
        db,
        iot_id,
        actor_id=actor_id,
        include_source=True,
    )


async def update_device_heartbeat(
    db: AsyncSession,
    *,
    iot_id: UUID,
    seen_at: datetime | None = None,
    firmware_version: str | None = None,
) -> None:
    """
    Atomically updates the last activity timestamp (last_seen_at) of the ESP32 node
    without row locking or unnecessary object fetching.
    """
    values: dict[str, Any] = {
        "last_seen_at": seen_at if seen_at is not None else func.now(),
    }
    has_firmware_update: bool = firmware_version is not None
    if has_firmware_update:
        values["firmware_version"] = firmware_version.strip() or None

    iot_device_stmt = (
        update(IoTDevice)
        .where(IoTDevice.iot_id == iot_id)
        .values(**values)
        .returning(IoTDevice.iot_id)
    )

    try:
        updated_id: UUID | None = await db.scalar(iot_device_stmt)
        is_device_missing: bool = updated_id is None
        if is_device_missing:
            raise IoTDeviceNotFoundError(f"IoT device {iot_id} not found.")
        await db.commit()
    except IoTDeviceNotFoundError:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateIoTDeviceError(
            f"Failed to update heartbeat for device {iot_id}"
        ) from exc


async def delete_iot_device(
    db: AsyncSession,
    *,
    iot_id: UUID,
    actor_id: UUID,
) -> None:
    """
    Deletes an IoT device from the database.
    """
    device: IoTDevice = await get_iot_device_by_id(
        db,
        iot_id,
        actor_id=actor_id,
    )

    device_source_id: UUID = cast(UUID, device.source_id)
    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=device_source_id,
        allowed_source_roles=(RoleName.OWNER.value,),
    )

    delete_iot_stmt = (
        delete(IoTDevice)
        .where(IoTDevice.iot_id == iot_id)
    )

    try:
        await db.execute(delete_iot_stmt)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToDeleteIoTDeviceError() from exc
