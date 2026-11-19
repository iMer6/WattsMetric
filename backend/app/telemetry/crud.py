from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import escape_like
from app.iot.exceptions import IoTDeviceInactiveError, IoTDeviceNotFoundError
from app.iot.models import IoTDevice
from app.sources.crud import ensure_user_source_permission
from app.sources.exceptions import SourceNotFoundError
from app.sources.models import Source
from app.telemetry.exceptions import *
from app.telemetry.models import Measurement, TestProfile, TimeAggregate

# Safe chunk size for batch insert (7 columns * 2000 rows = 14,000 parameters < 32,767 asyncpg limit)
TELEMETRY_BATCH_CHUNK_SIZE: int = 2000


def _validate_time_range(
    start_time: datetime,
    end_time: datetime
) -> None:
    has_missing_timezone: bool = (
        start_time.tzinfo is None
        or end_time.tzinfo is None
    )
    if has_missing_timezone:
        raise InvalidTelemetryPayloadError(
            "start_time and end_time must include timezone information."
        )

    is_invalid_range: bool = start_time > end_time
    if is_invalid_range:
        raise InvalidTimeRangeError()


# Measurements (Telemetry ingestion from ESP32 FIFO buffer and queries)


async def bulk_insert_measurements(
    db: AsyncSession,
    *,
    iot_id: UUID,
    records: Sequence[dict[str, Any]],
    expected_interval_seconds: int | None = None,
    update_heartbeat: bool = True,
) -> dict[str, int]:
    """
    High-performance batch insertion of raw telemetry measurements from the ESP32 FIFO buffer.
    - duplicate protection on buffer retries via ON CONFLICT (iot_id, measured_at) DO NOTHING.
    - Automatic chunking to prevent exceeding Postgres/asyncpg parameter limits
      when flushing long-term offline buffers (up to 72 hours).
    - automatic gap detection (is_gap=True) when expected_interval_seconds is provided,
      preserving flags already detected by the microcontroller.
    """
    device_stmt = (
        select(
            IoTDevice.iot_id,
            IoTDevice.is_active,
        )
        .where(IoTDevice.iot_id == iot_id)
    )
    device_result = await db.execute(device_stmt)
    device_row = device_result.first()

    if device_row is None:
        raise IoTDeviceNotFoundError(f"IoT device {iot_id} not found.")
    if not device_row.is_active:
        raise IoTDeviceInactiveError(
            f"IoT device {iot_id} is deactivated and cannot ingest telemetry."
        )

    if not records:
        return {
            "received": 0,
            "inserted": 0,
            "duplicates_ignored": 0,
            "gaps_detected": 0
        }

    # Sort batch by measurement timestamp for accurate gap analysis
    sorted_records: list[dict[str, Any]] = sorted(
        records, key=lambda record: record["measured_at"]
    )

    for item in sorted_records:
        is_naive_timestamp: bool = item["measured_at"].tzinfo is None
        if is_naive_timestamp:
            raise InvalidTelemetryPayloadError(
                "All measurement timestamps must be timezone-aware."
            )

    prev_measured_at: datetime | None = None
    max_gap_delta: timedelta | None = None
    has_valid_interval: bool = (
        expected_interval_seconds is not None
        and expected_interval_seconds > 0
    )

    if has_valid_interval:
        max_gap_delta: timedelta = timedelta(
            seconds=expected_interval_seconds * 1.5
        )
        first_ts: datetime = sorted_records[0]["measured_at"]
        prev_ts_stmt = (
            select(func.max(Measurement.measured_at))
            .where(
                and_(
                    Measurement.iot_id == iot_id,
                    Measurement.measured_at < first_ts,
                )
            )
        )
        prev_measured_at: datetime | None = await db.scalar(prev_ts_stmt)

    prepared_rows: list[dict[str, Any]] = []
    gaps_detected: int = 0

    for item in sorted_records:
        ts: datetime = item["measured_at"]
        is_gap: bool = bool(item.get("is_gap", False))

        is_exceeded_delta: bool = (
            not is_gap
            and max_gap_delta is not None
            and prev_measured_at is not None
            and (ts - prev_measured_at) > max_gap_delta
        )
        if is_exceeded_delta:
            is_gap = True

        if is_gap:
            gaps_detected += 1

        prepared_rows.append({
            "iot_id": iot_id,
            "measured_at": ts,
            "voltage": item.get("voltage"),
            "current": item.get("current"),
            "temperature": item.get("temperature"),
            "illumination": item.get("illumination"),
            "is_gap": is_gap,
        })
        prev_measured_at: datetime = ts

    inserted_total: int = 0

    try:
        for idx in range(
            0, len(prepared_rows), TELEMETRY_BATCH_CHUNK_SIZE
        ):
            chunk = prepared_rows[idx: idx + TELEMETRY_BATCH_CHUNK_SIZE]
            insert_stmt = (
                pg_insert(Measurement)
                .values(chunk)
                .on_conflict_do_nothing(
                    index_elements=["iot_id", "measured_at"]
                )
                .returning(Measurement.measured_at)
            )
            inserted_scalars = await db.scalars(insert_stmt)
            inserted_rows = inserted_scalars.all()
            inserted_total += len(inserted_rows)

        if update_heartbeat:
            heartbeat_stmt = (
                update(IoTDevice)
                .where(IoTDevice.iot_id == iot_id)
                .values(last_seen_at=func.now())
            )
            await db.execute(heartbeat_stmt)

        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToIngestTelemetryError() from exc

    received_count: int = len(prepared_rows)
    return {
        "received": received_count,
        "inserted": inserted_total,
        "duplicates_ignored": received_count - inserted_total,
        "gaps_detected": gaps_detected,
    }


async def get_measurements_range(
    db: AsyncSession,
    *,
    actor_id: UUID,
    iot_id: UUID,
    start_time: datetime,
    end_time: datetime,
    limit: int = 10000,
    only_gaps: bool = False,
) -> list[Measurement]:
    """
    Retrieves raw telemetry measurements for a specific device within a selected time range.
    
    Used for power calculation audits and gap diagnostics.
    """
    _validate_time_range(start_time, end_time)

    source_stmt = (
        select(IoTDevice.source_id)
        .where(IoTDevice.iot_id == iot_id)
    )
    source_id: UUID | None = await db.scalar(source_stmt)
    if source_id is None:
        raise IoTDeviceNotFoundError(f"IoT device {iot_id} not found.")

    await ensure_user_source_permission(
        db, user_id=actor_id, source_id=source_id
    )

    filters: list = [
        Measurement.iot_id == iot_id,
        Measurement.measured_at >= start_time,
        Measurement.measured_at <= end_time,
    ]
    if only_gaps:
        filters.append(Measurement.is_gap.is_(True))

    measurements_stmt = (
        select(Measurement)
        .where(and_(*filters))
        .order_by(Measurement.measured_at.asc())
        .limit(limit)
    )
    measurements_scalars = await db.scalars(measurements_stmt)
    return list(measurements_scalars.all())


async def get_measurement_gaps(
    db: AsyncSession,
    *,
    actor_id: UUID,
    iot_id: UUID,
    start_time: datetime,
    end_time: datetime,
    limit: int = 1000,
) -> list[Measurement]:
    """
    Fast retrieval of telemetry records with detected gaps (is_gap=True)
    utilizing the partial index idx_measurements_iot_id_measured_at_gap.
    """
    return await get_measurements_range(
        db,
        iot_id=iot_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        only_gaps=True,
        actor_id=actor_id,
    )


# Time Aggregates (Generation time aggregates and analytics)


async def upsert_time_aggregate(
    db: AsyncSession,
    *,
    iot_id: UUID,
    bucket: datetime,
    total_energy: Decimal,
    avg_voltage: Decimal | None = None,
    avg_current: Decimal | None = None,
    avg_temperature: Decimal | None = None,
    avg_illumination: Decimal | None = None,
) -> TimeAggregate:
    """
    Idempotently creates or updates an aggregated time interval (bucket) for an IoT node
    via Postgres ON CONFLICT (iot_id, bucket) DO UPDATE.
    """
    is_naive_bucket: bool = bucket.tzinfo is None
    if is_naive_bucket:
        raise InvalidTelemetryPayloadError(
            "Aggregate bucket timestamp must be timezone-aware."
        )

    device_exists_stmt = select(
        exists().where(IoTDevice.iot_id == iot_id)
    )
    device_exists: bool | None = await db.scalar(device_exists_stmt)
    if not device_exists:
        raise IoTDeviceNotFoundError(f"IoT device {iot_id} not found.")

    insert_stmt = pg_insert(TimeAggregate).values(
        iot_id=iot_id,
        bucket=bucket,
        total_energy=total_energy,
        avg_voltage=avg_voltage,
        avg_current=avg_current,
        avg_temperature=avg_temperature,
        avg_illumination=avg_illumination,
    )
    upsert_stmt = (
        insert_stmt.on_conflict_do_update(
            index_elements=["iot_id", "bucket"],
            set_={
                "total_energy": insert_stmt.excluded.total_energy,
                "avg_voltage": insert_stmt.excluded.avg_voltage,
                "avg_current": insert_stmt.excluded.avg_current,
                "avg_temperature": insert_stmt.excluded.avg_temperature,
                "avg_illumination": insert_stmt.excluded.avg_illumination,
            },
        )
        .returning(TimeAggregate)
    )

    try:
        aggregate: TimeAggregate | None = await db.scalar(upsert_stmt)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpsertAggregateError() from exc

    if aggregate is None:
        raise FailedToUpsertAggregateError()
    return aggregate


async def bulk_upsert_time_aggregates(
    db: AsyncSession,
    *,
    aggregates: Sequence[dict[str, Any]],
) -> int:
    """
    Batch idempotent update or insertion of time aggregates (for recalculation
    after backfilling data from the ESP32 offline buffer).
    """
    if not aggregates:
        return 0

    for item in aggregates:
        is_naive_bucket: bool = item["bucket"].tzinfo is None
        if is_naive_bucket:
            raise InvalidTelemetryPayloadError(
                "All aggregate bucket timestamps must be timezone-aware."
            )

    affected_total: int = 0
    chunk_size: int = 1500

    try:
        for idx in range(0, len(aggregates), chunk_size):
            chunk = list(aggregates[idx: idx + chunk_size])
            insert_stmt = pg_insert(TimeAggregate).values(chunk)
            upsert_stmt = (
                insert_stmt.on_conflict_do_update(
                    index_elements=["iot_id", "bucket"],
                    set_={
                        "total_energy": insert_stmt.excluded.total_energy,
                        "avg_voltage": insert_stmt.excluded.avg_voltage,
                        "avg_current": insert_stmt.excluded.avg_current,
                        "avg_temperature": insert_stmt.excluded.avg_temperature,
                        "avg_illumination": insert_stmt.excluded.avg_illumination,
                    },
                )
                .returning(TimeAggregate.bucket)
            )
            upsert_scalars = await db.scalars(upsert_stmt)
            rows = upsert_scalars.all()
            affected_total += len(rows)

        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise IoTDeviceNotFoundError(
            "One or more iot_id values in aggregates do not exist."
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpsertAggregateError() from exc

    return affected_total


async def get_time_aggregates_by_device(
    db: AsyncSession,
    *,
    actor_id: UUID,
    iot_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> list[TimeAggregate]:
    """
    Retrieves generation time aggregates
    for a specific measurement node over a given period.
    """
    _validate_time_range(start_time, end_time)

    source_stmt = (
        select(IoTDevice.source_id)
        .where(IoTDevice.iot_id == iot_id)
    )
    source_id: UUID | None = await db.scalar(source_stmt)
    if source_id is None:
        raise IoTDeviceNotFoundError(f"IoT device {iot_id} not found.")

    await ensure_user_source_permission(
        db, user_id=actor_id, source_id=source_id
    )

    aggregates_stmt = (
        select(TimeAggregate)
        .where(
            and_(
                TimeAggregate.iot_id == iot_id,
                TimeAggregate.bucket >= start_time,
                TimeAggregate.bucket <= end_time,
            )
        )
        .order_by(TimeAggregate.bucket.asc())
    )
    aggregates_scalars = await db.scalars(aggregates_stmt)
    return list(aggregates_scalars.all())


async def get_time_aggregates_by_source(
    db: AsyncSession,
    *,
    actor_id: UUID,
    source_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    """
    Summary time analytics across an entire generation facility (total energy and average
    parameters across all active station nodes grouped by time buckets) for building
    charts and comparing periods.
    """
    _validate_time_range(start_time, end_time)

    source_exists_stmt = select(
        exists().where(Source.source_id == source_id)
    )
    source_exists: bool | None = await db.scalar(source_exists_stmt)
    if not source_exists:
        raise SourceNotFoundError(f"Source {source_id} not found.")

    await ensure_user_source_permission(
        db, user_id=actor_id, source_id=source_id
    )

    aggregates_stmt = (
        select(
            TimeAggregate.bucket.label("bucket"),
            func.sum(TimeAggregate.total_energy).label("total_energy"),
            func.avg(TimeAggregate.avg_voltage).label("avg_voltage"),
            func.avg(TimeAggregate.avg_current).label("avg_current"),
            func.avg(TimeAggregate.avg_temperature).label("avg_temperature"),
            func.avg(TimeAggregate.avg_illumination).label("avg_illumination"),
        )
        .join(
            IoTDevice, IoTDevice.iot_id == TimeAggregate.iot_id
        )
        .where(
            and_(
                IoTDevice.source_id == source_id,
                TimeAggregate.bucket >= start_time,
                TimeAggregate.bucket <= end_time,
            )
        )
        .group_by(TimeAggregate.bucket)
        .order_by(TimeAggregate.bucket.asc())
    )

    aggregates_result = await db.execute(aggregates_stmt)
    rows = aggregates_result.all()
    return [
        {
            "bucket": row.bucket,
            "total_energy": row.total_energy,
            "avg_voltage": row.avg_voltage,
            "avg_current": row.avg_current,
            "avg_temperature": row.avg_temperature,
            "avg_illumination": row.avg_illumination,
        } for row in rows
    ]


# Test Profiles (Test profiles for simulation and calibration)


async def create_test_profile(
    db: AsyncSession,
    *,
    test_profile_name: str,
    profile_payload: dict[str, Any],
) -> TestProfile:
    """
    Saves a new JSON generation simulation scenario.
    """
    cleaned_name: str = test_profile_name.strip()

    name_exists_stmt = select(
        exists().where(TestProfile.test_profile_name == cleaned_name)
    )
    name_exists: bool | None = await db.scalar(name_exists_stmt)
    if name_exists:
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
    test_profile_id: UUID,
) -> TestProfile:
    """
    Retrieves a simulation test profile by UUID.
    """
    profile_stmt = (
        select(TestProfile)
        .where(TestProfile.test_profile_id == test_profile_id)
    )
    profile: TestProfile | None = await db.scalar(profile_stmt)
    if profile is None:
        raise TestProfileNotFoundError(
            f"Test profile {test_profile_id} not found."
        )
    return profile


async def get_test_profile_by_name(
    db: AsyncSession,
    test_profile_name: str,
) -> TestProfile:
    """
    Retrieves a simulation test profile by its unique name.
    """
    cleaned_name: str = test_profile_name.strip()
    profile_stmt = (
        select(TestProfile)
        .where(TestProfile.test_profile_name == cleaned_name)
    )
    profile: TestProfile | None = await db.scalar(profile_stmt)
    if profile is None:
        raise TestProfileNotFoundError(
            f"Test profile '{cleaned_name}' not found."
        )
    return profile


async def get_test_profiles(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> tuple[list[TestProfile], int]:
    """
    Returns a paginated list of all simulation test profiles.
    """
    filters: list[Any] = []
    has_search_query: bool = bool(search and search.strip())
    if has_search_query and search is not None:
        escaped: str = f"%{escape_like(search.strip())}%"
        filters.append(
            TestProfile.test_profile_name.ilike(escaped, escape="\\")
        )

    count_stmt = (
        select(func.count(TestProfile.test_profile_id))
        .where(*filters)
    )
    total_raw = await db.scalar(count_stmt)
    total: int = int(total_raw or 0)
    if total == 0:
        return [], 0

    profiles_stmt = (
        select(TestProfile)
        .where(*filters)
        .order_by(
            TestProfile.created_at.desc(),
            TestProfile.test_profile_id.asc()
        )
        .offset(skip)
        .limit(limit)
    )
    profiles_scalars = await db.scalars(profiles_stmt)
    profiles: list[TestProfile] = list(profiles_scalars.all())
    return profiles, total


async def delete_test_profile(
    db: AsyncSession,
    test_profile_id: UUID,
) -> None:
    """
    Deletes a simulation test profile.
    """
    exists_stmt = select(
        exists().where(TestProfile.test_profile_id == test_profile_id)
    )
    exists_flag: bool | None = await db.scalar(exists_stmt)
    if not exists_flag:
        raise TestProfileNotFoundError(
            f"Test profile {test_profile_id} not found."
        )

    try:
        delete_stmt = (
            delete(TestProfile)
            .where(TestProfile.test_profile_id == test_profile_id)
        )
        await db.execute(delete_stmt)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToManageTestProfileError() from exc
