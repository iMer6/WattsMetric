from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import normalise_mac_address

from app.iot.exceptions import (
    FailedToCreateIoTDeviceError,
    FailedToDeleteIoTDeviceError,
    FailedToUpdateIoTDeviceError,
    InvalidMacAddressError,
    IoTDeviceMacAlreadyExistsError,
    IoTDeviceNotFoundError,
)
from app.iot.models import IoTDevice


async def get_iot_devices_for_source(
    db: AsyncSession,
    *,
    source_id: UUID,
) -> list[IoTDevice]:
    """
    Returns all ESP32 IoT devices commissioned on the given energy source.
    """
    stmt = (
        select(IoTDevice)
        .where(IoTDevice.source_id == source_id)
        .order_by(
            IoTDevice.created_at.desc(),
            IoTDevice.iot_id.asc()
        )
    )
    result = await db.scalars(stmt)
    return list(result.all())


async def get_iot_device_by_id(
    db: AsyncSession,
    *,
    source_id: UUID,
    iot_id: UUID,
) -> IoTDevice:
    # noinspection SpellCheckingInspection
    """
    Fetches an IoT device strictly scoped to `source_id` (BOLA/IDOR protection).

    Raises:
        IoTDeviceNotFoundError: If the device does not exist or belongs to another source.
    """
    stmt = (
        select(IoTDevice)
        .where(
            and_(
                IoTDevice.iot_id == iot_id,
                IoTDevice.source_id == source_id,
            )
        )
    )
    device: IoTDevice | None = await db.scalar(stmt)
    if device is None:
        raise IoTDeviceNotFoundError(
            f"IoT device {iot_id} was not found on energy source {source_id}."
        )
    return device


async def create_iot_device(
    db: AsyncSession,
    *,
    source_id: UUID,
    esp32_mac: str,
    firmware_version: str,
    sensor_config: dict[str, Any],
    is_active: bool = True,
) -> IoTDevice:
    """
    Commissions a new ESP32 microcontroller onto an energy source.

    Raises:
        ValueError: If `esp32_mac` is not a normalised EUI-48 address.
        IoTDeviceMacAlreadyExistsError: If `esp32_mac` is already registered anywhere in the system.
    """
    try:
        normalised_mac: str = normalise_mac_address(esp32_mac)
    except ValueError as exc:
        raise InvalidMacAddressError(str(exc)) from exc

    mac_exists_stmt = select(
        exists().where(IoTDevice.esp32_mac == normalised_mac)
    )
    mac_exists: bool | None = await db.scalar(mac_exists_stmt)
    if mac_exists:
        raise IoTDeviceMacAlreadyExistsError(
            f"IoT device with MAC address '{normalised_mac}' is already registered."
        )

    device: IoTDevice = IoTDevice(
        source_id=source_id,
        esp32_mac=normalised_mac,
        firmware_version=firmware_version.strip(),
        sensor_config=sensor_config,
        is_active=is_active,
    )

    try:
        db.add(device)
        await db.commit()
        await db.refresh(device)
    except IntegrityError as exc:
        await db.rollback()
        is_esp32_mac_conflict: bool = "esp32_mac" in str(exc.orig).lower()
        if is_esp32_mac_conflict:
            raise IoTDeviceMacAlreadyExistsError(
                f"IoT device with MAC address '{normalised_mac}' is already registered."
            ) from exc
        raise FailedToCreateIoTDeviceError() from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToCreateIoTDeviceError() from exc

    return device


async def update_iot_device(
    db: AsyncSession,
    *,
    source_id: UUID,
    iot_id: UUID,
    firmware_version: str | None = None,
    sensor_config: dict[str, Any] | None = None,
    is_active: bool | None = None,
) -> IoTDevice:
    """
    Updates firmware version, sensor calibration JSONB config, or active status
    of an ESP32 IoT node attached to `source_id`.
    """
    stmt = (
        select(IoTDevice)
        .where(
            and_(
                IoTDevice.iot_id == iot_id,
                IoTDevice.source_id == source_id,
            )
        )
        .with_for_update()
    )
    device: IoTDevice | None = await db.scalar(stmt)
    if device is None:
        raise IoTDeviceNotFoundError(
            f"IoT device {iot_id} was not found on energy source {source_id}."
        )

    if firmware_version is not None:
        device.firmware_version = firmware_version.strip()
    if sensor_config is not None:
        device.sensor_config = sensor_config
    if is_active is not None:
        device.is_active = is_active

    try:
        await db.commit()
        await db.refresh(device)
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateIoTDeviceError() from exc

    return device


async def delete_iot_device(
    db: AsyncSession,
    *,
    source_id: UUID,
    iot_id: UUID,
) -> None:
    """
    Removes an ESP32 IoT device record strictly scoped to `source_id`.
    """
    stmt = (
        select(IoTDevice)
        .where(
            and_(
                IoTDevice.iot_id == iot_id,
                IoTDevice.source_id == source_id,
            )
        )
        .with_for_update()
    )
    device: IoTDevice | None = await db.scalar(stmt)
    if device is None:
        raise IoTDeviceNotFoundError(
            f"IoT device {iot_id} was not found on energy source {source_id}."
        )

    try:
        await db.delete(device)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToDeleteIoTDeviceError() from exc
