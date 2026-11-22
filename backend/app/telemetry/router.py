from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from app.core.schemas import PaginatedResponse

from app.telemetry import crud
from app.telemetry.dependencies import (
    AnalyticsReadableSource,
    ManageableSourceDevice,
    ReadableSourceDevice,
    TestProfileManagerUser,
)
from app.telemetry.models import TestProfile
from app.telemetry.schemas import (
    MeasurementBatchIngestRequest,
    MeasurementBatchIngestResponse,
    MeasurementOut,
    TestProfileCreateRequest,
    TestProfileDetailOut,
    TestProfileShortOut,
    TimeAggregateOut,
)
from app.users.dependencies import DbSession

router: APIRouter = APIRouter()


# Test Profiles


@router.get(
    "/test-profiles",
    tags=["Test Profiles"],
    response_model=PaginatedResponse[TestProfileShortOut],
    summary="List saved simulation and calibration test profiles",
    description=(
        "Returns a paginated list of saved test profiles used for time-series replay and "
        "anomaly alert rule calibration.\n\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, `OWNER`, `DISPATCHER`."
    ),
)
async def list_test_profiles(
    response: Response,
    db: DbSession,
    _: TestProfileManagerUser,
    skip: int = Query(
        default=0, ge=0,
        description="Number of records to skip."
    ),
    limit: int = Query(
        default=50, ge=1, le=100,
        description="Maximum number of records to return (1–100)."
    ),
    search: str | None = Query(
        default=None, max_length=100,
        description="Case-insensitive filter by test profile name.",
    ),
) -> PaginatedResponse[TestProfileShortOut]:
    profiles, total = await crud.get_test_profiles(
        db,
        skip=skip,
        limit=limit,
        search=search,
    )
    response.headers["X-Total-Count"] = str(total)
    return PaginatedResponse[TestProfileShortOut](
        items=[
            TestProfileShortOut.model_validate(profile)
            for profile in profiles
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/test-profiles",
    tags=["Test Profiles"],
    response_model=TestProfileDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new calibration test profile",
    description=(
        "Stores a synthetic or archived generation curve (`profile_payload` JSONB) for "
        "replaying power integration and testing alert rules.\n\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, `OWNER`, `DISPATCHER`."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Conflict — a test profile with this name already exists.",
        },
    },
)
async def create_test_profile(
    db: DbSession,
    _: TestProfileManagerUser,
    payload: TestProfileCreateRequest,
) -> TestProfileDetailOut:
    profile: TestProfile = await crud.create_test_profile(
        db,
        test_profile_name=payload.test_profile_name,
        profile_payload=payload.profile_payload,
    )
    return TestProfileDetailOut.model_validate(profile)


@router.get(
    "/test-profiles/{test_profile_id}",
    tags=["Test Profiles"],
    response_model=TestProfileDetailOut,
    summary="Get test profile with full replay payload",
    description=(
        "Retrieves a specific test profile including its full JSONB `profile_payload` "
        "for executing algorithm replay.\n\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, `OWNER`, `DISPATCHER`."
    ),
)
async def get_test_profile(
    db: DbSession,
    _: TestProfileManagerUser,
    test_profile_id: Annotated[
        UUID,
        Path(description="Target test profile UUID.")
    ],
) -> TestProfileDetailOut:
    profile: TestProfile = await crud.get_test_profile_by_id(
        db, test_profile_id=test_profile_id,
    )
    return TestProfileDetailOut.model_validate(profile)


# Telemetry & Time Aggregates


@router.post(
    "/{source_id}/devices/{iot_id}/measurements/batch",
    tags=["Telemetry & Aggregates"],
    response_model=MeasurementBatchIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest buffered telemetry batch from ESP32",
    description=(
        "Idempotently ingests a batch of up to **1000** synchronous sensor readings (`voltage`, "
        "`current`, `temperature`, `illumination`, `is_gap`) flushed from the ESP32 FIFO flash "
        "buffer after offline buffering.\n\n"
        "- **Idempotency & Offline Recovery:** Uses `ON CONFLICT (iot_id, measured_at) DO UPDATE` "
        "so retransmissions during unstable rural connectivity never fail or duplicate rows.\n"
        "- **Gap Control & Heartbeat:** Automatically flags unexpected sampling gaps (`is_gap=True`) "
        "and advances `iot_devices.last_seen_at`.\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, or `OWNER`/`ENGINEER` assigned to this facility."
    ),
)
async def ingest_telemetry_batch(
    db: DbSession,
    source_and_device: ManageableSourceDevice,
    payload: MeasurementBatchIngestRequest,
) -> MeasurementBatchIngestResponse:
    _, device = source_and_device
    summary: dict[str, Any] = await crud.ingest_measurements_batch(
        db,
        device=device,
        items=payload.measurements,
    )
    return MeasurementBatchIngestResponse(**summary)


@router.get(
    "/{source_id}/devices/{iot_id}/measurements",
    tags=["Telemetry & Aggregates"],
    response_model=PaginatedResponse[MeasurementOut],
    summary="Query raw telemetry measurements for an IoT device",
    description=(
        "Queries the raw telemetry archive (`measurements`) for a specific ESP32 node within a "
        "bounded UTC time interval.\n\n"
        "- **Performance Guard:** Requires `date_from` and `date_to` (max 31-day window) and "
        "paginates results with `X-Total-Count`.\n"
        "- **Gap Inspection:** Set `only_gaps=true` to inspect detected telemetry outages.\n"
        "- **Access Control:** `ROOT`, `ADMIN`, or assigned `OWNER`, `ENGINEER`, `DISPATCHER`."
    ),
)
async def list_device_measurements(
    response: Response,
    db: DbSession,
    source_and_device: ReadableSourceDevice,
    date_from: datetime = Query(
        ...,
        description="Inclusive start of UTC query interval (ISO 8601).",
        examples=["2026-11-22T00:00:00Z"],
    ),
    date_to: datetime = Query(
        ...,
        description=(
            "Inclusive end of UTC query interval (ISO 8601, "
            "max 31 days after `date_from`)."
        ),
        examples=["2026-11-22T23:59:59Z"],
    ),
    only_gaps: bool = Query(
        default=False,
        description=(
            "When true, returns only measurements marked with `is_gap=True`."
        ),
    ),
    skip: int = Query(
        default=0, ge=0,
        description="Number of records to skip."
    ),
    limit: int = Query(
        default=100, ge=1, le=1000,
        description="Maximum number of measurements to return per page (1–1000).",
    ),
) -> PaginatedResponse[MeasurementOut]:
    _, device = source_and_device
    items, total = await crud.get_measurements(
        db,
        iot_id=device.iot_id,
        date_from=date_from,
        date_to=date_to,
        only_gaps=only_gaps,
        skip=skip,
        limit=limit,
    )
    response.headers["X-Total-Count"] = str(total)
    return PaginatedResponse[MeasurementOut](
        items=[MeasurementOut.model_validate(m) for m in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{source_id}/aggregates",
    tags=["Telemetry & Aggregates"],
    response_model=PaginatedResponse[TimeAggregateOut],
    summary="Query integrated energy and environmental time aggregates",
    description=(
        "Retrieves pre-aggregated energy production (`total_energy` in kWh) and environmental "
        "averages (`avg_voltage`, `avg_current`, `avg_temperature`, `avg_illumination`) for "
        "dashboard charts and calculation audits.\n\n"
        "- **Access Control:** Requires `analytics:view` (`ROOT`, `ADMIN`, or assigned `OWNER` / "
        "`DISPATCHER`)."
    ),
)
async def list_source_aggregates(
    response: Response,
    db: DbSession,
    source: AnalyticsReadableSource,
    date_from: datetime = Query(
        ...,
        description="Inclusive start of UTC aggregation range (ISO 8601).",
        examples=["2026-11-01T00:00:00Z"],
    ),
    date_to: datetime = Query(
        ...,
        description=(
            "Inclusive end of UTC aggregation range (ISO 8601, max 366 days)."
        ),
        examples=["2026-11-22T23:59:59Z"],
    ),
    iot_id: UUID | None = Query(
        default=None,
        description=(
            "Optional filter for a specific ESP32 node attached to this source."
        ),
    ),
    skip: int = Query(
        default=0, ge=0,
        description="Number of records to skip."
    ),
    limit: int = Query(
        default=100, ge=1, le=1000,
        description=(
            "Maximum number of aggregate buckets to return per page (1–1000)."
        ),
    ),
) -> PaginatedResponse[TimeAggregateOut]:
    items, total = await crud.get_time_aggregates(
        db,
        source_id=source.source_id,
        date_from=date_from,
        date_to=date_to,
        iot_id=iot_id,
        skip=skip,
        limit=limit,
    )
    response.headers["X-Total-Count"] = str(total)
    return PaginatedResponse[TimeAggregateOut](
        items=[TimeAggregateOut.model_validate(a) for a in items],
        total=total,
        skip=skip,
        limit=limit,
    )
