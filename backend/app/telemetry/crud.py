from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import escape_like
from app.iot.crud import get_iot_device_by_id
from app.iot.exceptions import IoTDeviceInactiveError
from app.iot.models import IoTDevice
from app.telemetry.exceptions import (
    FailedToIngestTelemetryError,
    FailedToManageTestProfileError,
    InvalidTelemetryPayloadError,
    InvalidTimeRangeError,
    TelemetryBatchLimitExceededError,
    TestProfileAlreadyExistsError,
    TestProfileNotFoundError,
    TimeRangeTooLargeError,
)
from app.telemetry.models import Measurement, TestProfile, TimeAggregate
from app.telemetry.schemas import MAX_TELEMETRY_BATCH_SIZE, MeasurementCreateItem

MAX_RAW_MEASUREMENTS_WINDOW: timedelta = timedelta(days=31)
MAX_AGGREGATES_WINDOW: timedelta = timedelta(days=366)


def _is_timezone_aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def _validate_time_window(
    date_from: datetime,
    date_to: datetime,
    *,
    max_window: timedelta,
) -> tuple[datetime, datetime]:
    is_date_from_naive: bool = not _is_timezone_aware(date_from)
    if is_date_from_naive:
        raise InvalidTelemetryPayloadError(
            "'date_from' must be timezone-aware (UTC)."
        )

    is_date_to_naive: bool = not _is_timezone_aware(date_to)
    if is_date_to_naive:
        raise InvalidTelemetryPayloadError(
            "'date_to' must be timezone-aware (UTC)."
        )

    utc_from: datetime = date_from.astimezone(timezone.utc)
    utc_to: datetime = date_to.astimezone(timezone.utc)

    is_invalid_range: bool = utc_from > utc_to
    if is_invalid_range:
        raise InvalidTimeRangeError(
            "'date_from' must be earlier than or equal to 'date_to'."
        )

    is_window_exceeded: bool = (utc_to - utc_from) > max_window
    if is_window_exceeded:
        raise TimeRangeTooLargeError(
            f"Requested time interval exceeds the max allowed window of {max_window.days} days."
        )

    return utc_from, utc_to


# Telemetry & Time Aggregates CRUD


async def ingest_measurements_batch(
    db: AsyncSession,
    *,
    device: IoTDevice,
    items: Sequence[MeasurementCreateItem],
) -> dict[str, Any]:
    """
    Idempotently ingests a batch of buffered telemetry measurements from an ESP32 node.

    - Enforces maximum batch size (`MAX_TELEMETRY_BATCH_SIZE`) and device active state.
    - Deduplicates intra-batch timestamps to prevent Postgres `ON CONFLICT` cardinality errors.
    - Preserves device `is_gap` markers and automatically flags gaps exceeding 2x the
          configured `sampling_interval_seconds` in `device.sensor_config`.
    - Performs bulk `INSERT ... ON CONFLICT (iot_id, measured_at) DO UPDATE` and updates
          `IoTDevice.last_seen_at` atomically.
    """
    is_device_inactive: bool = not device.is_active
    if is_device_inactive:
        raise IoTDeviceInactiveError(
            f"IoT device {device.iot_id} is deactivated and cannot ingest telemetry."
        )

    received_count: int = len(items)
    is_batch_size_invalid: bool = (
        received_count == 0 or received_count > MAX_TELEMETRY_BATCH_SIZE
    )
    if is_batch_size_invalid:
        raise TelemetryBatchLimitExceededError(
            f"Batch must contain between 1 and {MAX_TELEMETRY_BATCH_SIZE} records."
        )

    dedup_by_ts: dict[datetime, MeasurementCreateItem] = {}
    for item in items:
        is_timestamp_naive: bool = not _is_timezone_aware(item.measured_at)
        if is_timestamp_naive:
            raise InvalidTelemetryPayloadError()
        dedup_by_ts[item.measured_at.astimezone(timezone.utc)] = item

    ordered_items: list[MeasurementCreateItem] = sorted(
        dedup_by_ts.values(),
        key=lambda x: x.measured_at,
    )

    sampling_interval_raw: Any = (device.sensor_config or {}).get(
        "sampling_interval_seconds"
    )
    is_valid_interval: bool = (
        isinstance(sampling_interval_raw, (int, float))
        and sampling_interval_raw > 0
    )
    expected_interval_sec: float | None = (
        float(sampling_interval_raw) if is_valid_interval else None
    )

    rows: list[dict[str, Any]] = []
    prev_ts: datetime | None = device.last_seen_at
    gap_count: int = 0

    for item in ordered_items:
        ts_utc: datetime = item.measured_at.astimezone(timezone.utc)
        computed_gap: bool = item.is_gap

        can_detect_gap: bool = (
            not computed_gap
            and expected_interval_sec is not None
            and prev_ts is not None
        )
        if can_detect_gap:
            delta_sec: float = (ts_utc - prev_ts).total_seconds()
            is_gap_exceeded: bool = delta_sec > (expected_interval_sec * 2.0)
            if is_gap_exceeded:
                computed_gap = True

        if computed_gap:
            gap_count += 1

        rows.append({
            "iot_id": device.iot_id,
            "measured_at": ts_utc,
            "voltage": item.voltage,
            "current": item.current,
            "temperature": item.temperature,
            "illumination": item.illumination,
            "is_gap": computed_gap,
        })
        prev_ts = ts_utc

    latest_measured_at: datetime = (
        ordered_items[-1].measured_at.astimezone(timezone.utc)
    )

    insert_stmt = pg_insert(Measurement).values(rows)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["iot_id", "measured_at"],
        set_={
            "voltage": insert_stmt.excluded.voltage,
            "current": insert_stmt.excluded.current,
            "temperature": insert_stmt.excluded.temperature,
            "illumination": insert_stmt.excluded.illumination,
            "is_gap": insert_stmt.excluded.is_gap,
        },
    )

    try:
        await db.execute(upsert_stmt)

        device_stmt = (
            select(IoTDevice)
            .where(IoTDevice.iot_id == device.iot_id)
            .with_for_update()
        )
        locked_device: IoTDevice | None = await db.scalar(device_stmt)
        
        if locked_device is not None:
            should_update_last_seen: bool = (
                locked_device.last_seen_at is None
                or latest_measured_at > locked_device.last_seen_at
            )
            
            if should_update_last_seen:
                locked_device.last_seen_at = latest_measured_at

        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToIngestTelemetryError() from exc

    return {
        "iot_id": device.iot_id,
        "received_count": received_count,
        "processed_count": len(rows),
        "gap_count": gap_count,
        "latest_measured_at": latest_measured_at,
    }


async def get_measurements(
    db: AsyncSession,
    *,
    iot_id: UUID,
    date_from: datetime,
    date_to: datetime,
    only_gaps: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Measurement], int]:
    """
    Retrieves paginated raw telemetry measurements for an IoT node within a bounded UTC range.
    """
    utc_from, utc_to = _validate_time_window(
        date_from,
        date_to,
        max_window=MAX_RAW_MEASUREMENTS_WINDOW,
    )

    filters: list[Any] = [
        Measurement.iot_id == iot_id,
        Measurement.measured_at >= utc_from,
        Measurement.measured_at <= utc_to,
    ]
    if only_gaps:
        filters.append(Measurement.is_gap.is_(True))

    count_stmt = (
        select(func.count())
        .select_from(Measurement)
        .where(*filters)
    )
    raw_total: int | None = await db.scalar(count_stmt)
    total: int = int(raw_total or 0)

    is_empty_result: bool = total == 0
    if is_empty_result:
        return [], 0

    data_stmt = (
        select(Measurement)
        .where(*filters)
        .order_by(Measurement.measured_at.asc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.scalars(data_stmt)
    return list(result.all()), total


async def get_time_aggregates(
    db: AsyncSession,
    *,
    source_id: UUID,
    date_from: datetime,
    date_to: datetime,
    iot_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[TimeAggregate], int]:
    """
    Retrieves paginated energy and environmental time aggregates (`time_aggregates`)
    for an energy source (optionally filtered to a specific `iot_id` belonging to `source_id`).
    """
    utc_from, utc_to = _validate_time_window(
        date_from,
        date_to,
        max_window=MAX_AGGREGATES_WINDOW,
    )

    has_iot_filter: bool = iot_id is not None
    if has_iot_filter:
        await get_iot_device_by_id(db, source_id=source_id, iot_id=iot_id)

    filters: list[Any] = [
        IoTDevice.source_id == source_id,
        TimeAggregate.bucket >= utc_from,
        TimeAggregate.bucket <= utc_to,
    ]
    if has_iot_filter:
        filters.append(TimeAggregate.iot_id == iot_id)

    count_stmt = (
        select(func.count())
        .select_from(TimeAggregate)
        .join(
            IoTDevice,
            IoTDevice.iot_id == TimeAggregate.iot_id
        )
        .where(*filters)
    )
    raw_total: int | None = await db.scalar(count_stmt)
    total: int = int(raw_total or 0)

    is_empty_result: bool = total == 0
    if is_empty_result:
        return [], 0

    agg_stmt = (
        select(TimeAggregate)
        .join(
            IoTDevice,
            IoTDevice.iot_id == TimeAggregate.iot_id
        )
        .where(*filters)
        .order_by(
            TimeAggregate.bucket.asc(),
            TimeAggregate.iot_id.asc()
        )
        .offset(skip)
        .limit(limit)
    )
    result = await db.scalars(agg_stmt)
    return list(result.all()), total


# Test Profiles CRUD


async def get_test_profiles(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> tuple[list[TestProfile], int]:
    """
    Returns a paginated list of saved calibration/replay test profiles.
    """
    filters: list[Any] = []
    trimmed_search: str = search.strip() if search else ""
    has_search_query: bool = bool(trimmed_search)
    if has_search_query:
        escaped: str = f"%{escape_like(trimmed_search)}%"
        filters.append(
            TestProfile.test_profile_name.ilike(escaped, escape="\\")
        )

    count_stmt = (
        select(func.count(TestProfile.test_profile_id))
        .where(*filters)
    )
    raw_total: int | None = await db.scalar(count_stmt)
    total: int = int(raw_total or 0)

    is_empty_result: bool = total == 0
    if is_empty_result:
        return [], 0

    stmt = (
        select(TestProfile)
        .where(*filters)
        .order_by(
            TestProfile.created_at.desc(),
            TestProfile.test_profile_id.asc(),
        )
        .offset(skip)
        .limit(limit)
    )
    result = await db.scalars(stmt)
    return list(result.all()), total


async def create_test_profile(
    db: AsyncSession,
    *,
    test_profile_name: str,
    profile_payload: dict[str, Any],
) -> TestProfile:
    """
    Creates a new synthetic or archived test profile for replay calibration.

    Raises:
        TestProfileAlreadyExistsError: If `test_profile_name` already exists.
    """
    cleaned_name: str = test_profile_name.strip()

    exists_stmt = select(
        exists().where(TestProfile.test_profile_name == cleaned_name)
    )
    profile_already_exists: bool = bool(await db.scalar(exists_stmt))
    
    if profile_already_exists:
        raise TestProfileAlreadyExistsError(
            f"Test profile '{cleaned_name}' already exists."
        )

    profile: TestProfile = TestProfile(
        test_profile_name=cleaned_name,
        profile_payload=profile_payload,
    )

    try:
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    except IntegrityError as exc:
        await db.rollback()
        is_duplicate_name_error: bool = (
            "test_profile_name" in str(exc.orig).lower()
        )
        
        if is_duplicate_name_error:
            raise TestProfileAlreadyExistsError(
                f"Test profile '{cleaned_name}' already exists."
            ) from exc
        raise FailedToManageTestProfileError() from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToManageTestProfileError() from exc

    return profile


async def get_test_profile_by_id(
    db: AsyncSession,
    *,
    test_profile_id: UUID,
) -> TestProfile:
    """
    Fetches a complete test profile including its JSONB `profile_payload` for replay.
    """
    stmt = (
        select(TestProfile)
        .where(TestProfile.test_profile_id == test_profile_id)
    )
    profile: TestProfile | None = await db.scalar(stmt)

    is_profile_missing: bool = profile is None
    
    if is_profile_missing:
        raise TestProfileNotFoundError(
            f"Test profile {test_profile_id} not found."
        )
    return profile
