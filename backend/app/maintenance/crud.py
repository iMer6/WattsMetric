from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.enums import RoleName
from app.maintenance.exceptions import (
    FailedToSyncMaintenanceError,
    InvalidInspectorAssignmentError,
    InvalidMaintenanceTimestampError,
    MaintenanceSessionNotFoundError,
)
from app.maintenance.models import (
    MaintenanceInspector,
    MaintenancePhoto,
    MaintenanceSession,
)
from app.sources.crud import ensure_user_source_permission
from app.sources.exceptions import SourceNotFoundError
from app.sources.models import Source
from app.telemetry.models import Measurement, TimeAggregate
from app.users.models import User

SYSTEM_ROLE_NAMES: frozenset[str] = frozenset({
    RoleName.ROOT.value,
    RoleName.ADMIN.value,
})


async def _validate_inspectors_for_source(
    db: AsyncSession,
    *,
    source_id: UUID,
    inspector_ids: Sequence[UUID],
) -> list[UUID]:
    """
    Validates that each inspector:
    1) exists and is active (is_active=True);
    2) holds the global ENGINEER role or a system role (ROOT/ADMIN);
    3) has access to the source_id object (or is ROOT/ADMIN).
    """
    unique_ids: list[UUID] = list(dict.fromkeys(inspector_ids))
    has_inspectors: bool = bool(unique_ids)
    if not has_inspectors:
        return []

    users_stmt = (
        select(User)
        .where(User.user_id.in_(unique_ids))
        .options(
            selectinload(User.roles),
            selectinload(User.source_accesses),
        )
    )
    users: list[User] = list((await db.scalars(users_stmt)).all())

    all_inspectors_exist: bool = len(users) == len(unique_ids)
    if not all_inspectors_exist:
        raise InvalidInspectorAssignmentError(
            "One or more specified inspector user IDs do not exist."
        )

    for user in users:
        is_user_active: bool = user.is_active
        if not is_user_active:
            raise InvalidInspectorAssignmentError(
                f"Inspector {user.user_id} is deactivated."
            )

        role_names: set[str] = {role.role_name for role in user.roles}
        is_system: bool = bool(role_names & SYSTEM_ROLE_NAMES)
        has_engineer_role: bool = RoleName.ENGINEER.value in role_names
        is_authorized_role: bool = is_system or has_engineer_role

        if not is_authorized_role:
            raise InvalidInspectorAssignmentError(
                f"User {user.user_id} does not have the ENGINEER role."
            )

        if not is_system:
            has_source_access: bool = any(
                access.source_id == source_id
                for access in user.source_accesses
            )
            if not has_source_access:
                raise InvalidInspectorAssignmentError(
                    f"Inspector {user.user_id} does not have access "
                    f"to source {source_id}."
                )

    return unique_ids


async def sync_maintenance_session(
    db: AsyncSession,
    *,
    actor_id: UUID,
    session_id: UUID,
    source_id: UUID,
    created_at: datetime,
    inspector_notes: str | None = None,
    inspector_ids: Sequence[UUID] = (),
) -> MaintenanceSession:
    """
    Idempotent synchronization of a maintenance session from the mobile app.
    - Uses a client-generated UUID from Android (session_id).
    - Prevents overwriting sessions that have already been deactivated.
    - On re-sync, updates notes, the assigned inspector team,
      and refreshes the synced_at timestamp while setting is_synced=True.
    """
    is_timezone_aware: bool = created_at.tzinfo is not None
    if not is_timezone_aware:
        raise InvalidMaintenanceTimestampError(
            "Maintenance session created_at must be timezone-aware."
        )

    source_exists_stmt = select(
        exists().where(Source.source_id == source_id)
    )
    source_exists: bool = bool(await db.scalar(source_exists_stmt))
    if not source_exists:
        raise SourceNotFoundError(f"Source {source_id} not found.")

    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=source_id,
        allowed_source_roles=(
            RoleName.OWNER.value,
            RoleName.ENGINEER.value
        ),
    )

    deactivated_session_stmt = select(
        exists().where(
            and_(
                MaintenanceSession.session_id == session_id,
                MaintenanceSession.deleted_at.is_not(None),
            )
        )
    )
    is_session_deactivated: bool = bool(
        await db.scalar(deactivated_session_stmt)
    )
    if is_session_deactivated:
        raise MaintenanceSessionNotFoundError(
            f"Maintenance session {session_id} has been deactivated "
            f"and cannot be modified."
        )

    combined_inspector_ids: list[UUID] = list(inspector_ids)
    is_actor_in_inspectors: bool = actor_id in combined_inspector_ids
    if not is_actor_in_inspectors:
        combined_inspector_ids.append(actor_id)

    valid_inspector_ids: list[UUID] = await _validate_inspectors_for_source(
        db,
        source_id=source_id,
        inspector_ids=combined_inspector_ids,
    )

    insert_session_stmt = pg_insert(MaintenanceSession).values(
        session_id=session_id,
        source_id=source_id,
        inspector_notes=inspector_notes,
        is_synced=True,
        created_at=created_at,
        synced_at=func.now(),
    )
    upsert_session_stmt = insert_session_stmt.on_conflict_do_update(
        index_elements=["session_id"],
        set_={
            "inspector_notes": insert_session_stmt.excluded.inspector_notes,
            "is_synced": True,
            "synced_at": func.now(),
        },
        where=MaintenanceSession.deleted_at.is_(None),
    )

    try:
        await db.execute(upsert_session_stmt)

        has_valid_inspectors: bool = bool(valid_inspector_ids)
        if has_valid_inspectors:
            delete_inspectors_stmt = (
                delete(MaintenanceInspector)
                .where(
                    and_(
                        MaintenanceInspector.session_id == session_id,
                        MaintenanceInspector.inspector_id.notin_(
                            valid_inspector_ids
                        ),
                    )
                )
            )
            await db.execute(delete_inspectors_stmt)

            inspector_rows: list[dict[str, UUID]] = [
                {
                    "session_id": session_id,
                    "inspector_id": uid
                } for uid in valid_inspector_ids
            ]
            insert_inspectors_stmt = (
                pg_insert(MaintenanceInspector)
                .values(inspector_rows)
                .on_conflict_do_nothing(
                    index_elements=["session_id", "inspector_id"]
                )
            )
            await db.execute(insert_inspectors_stmt)

        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToSyncMaintenanceError() from exc

    return await get_maintenance_session_by_id(
        db, session_id, actor_id=actor_id
    )


async def add_maintenance_photo(
    db: AsyncSession,
    *,
    actor_id: UUID,
    session_id: UUID,
    photo_url: str,
    created_at: datetime,
    photo_id: UUID | None = None,
) -> MaintenancePhoto:
    """
    Idempotent persistence of uploaded inspection photo metadata.
    Supports a client-generated UUID (photo_id) for offline upload retries.
    Only attaches photos to active (non-deactivated) maintenance sessions.
    """
    is_timezone_aware: bool = created_at.tzinfo is not None
    if not is_timezone_aware:
        raise InvalidMaintenanceTimestampError(
            "Maintenance photo created_at must be timezone-aware."
        )

    source_id_stmt = (
        select(MaintenanceSession.source_id)
        .where(
            and_(
                MaintenanceSession.session_id == session_id,
                MaintenanceSession.deleted_at.is_(None),
            )
        )
    )
    source_id: UUID | None = await db.scalar(source_id_stmt)

    session_exists: bool = source_id is not None
    if not session_exists:
        raise MaintenanceSessionNotFoundError(
            f"Active maintenance session {session_id} not found."
        )

    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=source_id,
        allowed_source_roles=(
            RoleName.OWNER.value,
            RoleName.ENGINEER.value
        ),
    )

    values: dict[str, Any] = {
        "session_id": session_id,
        "photo_url": photo_url.strip(),
        "created_at": created_at,
        "uploaded_at": func.now(),
    }
    has_photo_id: bool = photo_id is not None
    if has_photo_id:
        values["photo_id"] = photo_id

    insert_photo_stmt = pg_insert(MaintenancePhoto).values(**values)
    upsert_photo_stmt = (
        insert_photo_stmt.on_conflict_do_update(
            index_elements=["photo_id"],
            set_={
                "photo_url": insert_photo_stmt.excluded.photo_url,
                "uploaded_at": func.now(),
            },
            where=MaintenancePhoto.deleted_at.is_(None),
        )
        .returning(MaintenancePhoto)
    )

    try:
        photo: MaintenancePhoto | None = await db.scalar(upsert_photo_stmt)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToSyncMaintenanceError(
            "Failed to save maintenance photo metadata."
        ) from exc

    is_photo_saved: bool = photo is not None
    if not is_photo_saved:
        raise FailedToSyncMaintenanceError()
    return photo


async def deactivate_maintenance_session(
    db: AsyncSession,
    *,
    actor_id: UUID,
    session_id: UUID,
) -> MaintenanceSession:
    """
    Soft-deletes (deactivates) a maintenance session and its attached photos
    by setting deleted_at and deleted_by, preserving the audit trail.
    """
    session_stmt = (
        select(MaintenanceSession)
        .where(
            and_(
                MaintenanceSession.session_id == session_id,
                MaintenanceSession.deleted_at.is_(None),
            )
        )
        .options(selectinload(MaintenanceSession.photos))
    )
    session: MaintenanceSession | None = await db.scalar(session_stmt)

    session_found: bool = session is not None
    if not session_found:
        raise MaintenanceSessionNotFoundError(
            f"Active maintenance session {session_id} not found."
        )
    
    session_source_id: UUID = cast(UUID, session.source_id)
    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=session_source_id,
        allowed_source_roles=(
            RoleName.OWNER.value,
            RoleName.ENGINEER.value
        ),
    )

    now_expr = func.now()
    session.deleted_at = now_expr
    session.deleted_by = actor_id

    for photo in session.photos:
        is_photo_active: bool = photo.deleted_at is None
        if is_photo_active:
            photo.deleted_at = now_expr
            photo.deleted_by = actor_id

    try:
        await db.commit()
        await db.refresh(session)
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToSyncMaintenanceError(
            "Failed to deactivate maintenance session."
        ) from exc

    return session


async def deactivate_maintenance_photo(
    db: AsyncSession,
    *,
    actor_id: UUID,
    photo_id: UUID,
) -> MaintenancePhoto:
    """
    Soft-deletes (deactivates) a specific maintenance photo by setting
    deleted_at and deleted_by while keeping the file metadata for audit.
    """
    photo_stmt = (
        select(MaintenancePhoto)
        .where(
            and_(
                MaintenancePhoto.photo_id == photo_id,
                MaintenancePhoto.deleted_at.is_(None),
            )
        )
        .options(joinedload(MaintenancePhoto.session))
    )
    photo: MaintenancePhoto | None = await db.scalar(photo_stmt)

    photo_found: bool = photo is not None
    if not photo_found:
        raise MaintenanceSessionNotFoundError(
            f"Active maintenance photo {photo_id} not found."
        )
    
    photo_session_source_id: UUID = (
        cast(MaintenanceSession, photo.session).source_id
    )
    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=photo_session_source_id,
        allowed_source_roles=(
            RoleName.OWNER.value,
            RoleName.ENGINEER.value
        ),
    )

    photo.deleted_at = func.now()
    photo.deleted_by = actor_id

    try:
        await db.commit()
        await db.refresh(photo)
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToSyncMaintenanceError(
            "Failed to deactivate maintenance photo."
        ) from exc

    return photo


async def get_maintenance_session_by_id(
    db: AsyncSession,
    session_id: UUID,
    *,
    actor_id: UUID,
    include_deactivated: bool = False,
) -> MaintenanceSession:
    """
    Retrieves complete maintenance session details with eager loading of
    inspectors, active photos, and station data (avoiding the N+1 problem).
    """
    filters: list = [MaintenanceSession.session_id == session_id]
    if not include_deactivated:
        filters.append(MaintenanceSession.deleted_at.is_(None))

    session_stmt = (
        select(MaintenanceSession)
        .where(and_(*filters))
        .options(
            joinedload(MaintenanceSession.source),
            selectinload(MaintenanceSession.inspectors).selectinload(User.roles),
            selectinload(
                MaintenanceSession.photos.and_(
                    MaintenancePhoto.deleted_at.is_(None)
                )
            ),
        )
    )
    session: MaintenanceSession | None = await db.scalar(session_stmt)

    session_found: bool = session is not None
    if not session_found:
        raise MaintenanceSessionNotFoundError(
            f"Maintenance session {session_id} not found."
        )

    session_source_id: UUID = cast(UUID, session.source_id)
    await ensure_user_source_permission(
        db, user_id=actor_id, source_id=session_source_id
    )

    return session


async def get_maintenance_history_by_source(
    db: AsyncSession,
    *,
    actor_id: UUID,
    source_id: UUID,
    skip: int = 0,
    limit: int = 20,
    include_deactivated: bool = False,
) -> tuple[list[MaintenanceSession], int]:
    """
    Returns paginated maintenance history for a station along with
    photo documentation and the list of inspectors for the web work log.
    Excludes deactivated records by default.
    """
    source_exists_stmt = select(
        exists().where(Source.source_id == source_id)
    )
    source_exists: bool = bool(await db.scalar(source_exists_stmt))
    if not source_exists:
        raise SourceNotFoundError(f"Source {source_id} not found.")

    await ensure_user_source_permission(
        db, user_id=actor_id, source_id=source_id
    )

    filters: list = [MaintenanceSession.source_id == source_id]
    if not include_deactivated:
        filters.append(MaintenanceSession.deleted_at.is_(None))

    count_stmt = (
        select(func.count(MaintenanceSession.session_id))
        .where(and_(*filters))
    )
    total: int = int(await db.scalar(count_stmt) or 0)

    has_records: bool = total > 0
    if not has_records:
        return [], 0

    sessions_stmt = (
        select(MaintenanceSession)
        .where(and_(*filters))
        .options(
            selectinload(MaintenanceSession.inspectors),
            selectinload(
                MaintenanceSession.photos.and_(
                    MaintenancePhoto.deleted_at.is_(None)
                )
            ),
        )
        .order_by(
            MaintenanceSession.created_at.desc(),
            MaintenanceSession.session_id.asc(),
        )
        .offset(skip)
        .limit(limit)
    )
    sessions: list[MaintenanceSession] = list(
        (await db.scalars(sessions_stmt)).all()
    )
    return sessions, total


async def get_source_rag_diagnostic_context(
    db: AsyncSession,
    *,
    actor_id: UUID,
    source_id: UUID,
    start_time: datetime,
    end_time: datetime,
    measurements_limit: int = 120,
    sessions_limit: int = 5,
) -> dict[str, Any]:
    """
    Builds a comprehensive factual RAG context for a generation facility
    to be passed to the Google Gemini API.
    Combines:
    1) station parameters and IoT node sensor configurations;
    2) generation time aggregates (total_energy, avg_illumination, avg_temperature) over the analysis window;
    3) recent raw measurements and the count of detected data gaps (is_gap=True);
    4) recent active entries from the maintenance log (inspector_notes + photo documentation).
    """
    source_stmt = (
        select(Source)
        .where(Source.source_id == source_id)
        .options(selectinload(Source.iot_devices))
    )
    source: Source | None = await db.scalar(source_stmt)

    source_found: bool = source is not None
    if not source_found:
        raise SourceNotFoundError(f"Source {source_id} not found.")

    await ensure_user_source_permission(
        db, user_id=actor_id, source_id=source_id
    )

    device_ids: list[UUID] = [dev.iot_id for dev in source.iot_devices]
    has_devices: bool = bool(device_ids)

    aggregates: list[TimeAggregate] = []
    recent_measurements: list[Measurement] = []
    gap_count: int = 0

    if has_devices:
        agg_stmt = (
            select(TimeAggregate)
            .where(
                and_(
                    TimeAggregate.iot_id.in_(device_ids),
                    TimeAggregate.bucket >= start_time,
                    TimeAggregate.bucket <= end_time,
                )
            )
            .order_by(TimeAggregate.bucket.desc())
            .limit(200)
        )
        aggregates = list((await db.scalars(agg_stmt)).all())

        meas_stmt = (
            select(Measurement)
            .where(
                and_(
                    Measurement.iot_id.in_(device_ids),
                    Measurement.measured_at >= start_time,
                    Measurement.measured_at <= end_time,
                )
            )
            .order_by(Measurement.measured_at.desc())
            .limit(measurements_limit)
        )
        recent_measurements = list((await db.scalars(meas_stmt)).all())

        gap_count_stmt = (
            select(func.count())
            .where(
                and_(
                    Measurement.iot_id.in_(device_ids),
                    Measurement.measured_at >= start_time,
                    Measurement.measured_at <= end_time,
                    Measurement.is_gap.is_(True),
                )
            )
        )
        gap_count = int(await db.scalar(gap_count_stmt) or 0)

    sessions_stmt = (
        select(MaintenanceSession)
        .where(
            and_(
                MaintenanceSession.source_id == source_id,
                MaintenanceSession.deleted_at.is_(None),
            )
        )
        .options(
            selectinload(MaintenanceSession.inspectors),
            selectinload(
                MaintenanceSession.photos.and_(
                    MaintenancePhoto.deleted_at.is_(None)
                )
            ),
        )
        .order_by(MaintenanceSession.created_at.desc())
        .limit(sessions_limit)
    )
    recent_sessions: list[MaintenanceSession] = list(
        (await db.scalars(sessions_stmt)).all()
    )

    return {
        "source": source,
        "iot_devices": source.iot_devices,
        "analysis_window": {
            "start_time": start_time,
            "end_time": end_time,
        },
        "telemetry_stats": {
            "detected_gaps_count": gap_count,
            "aggregates": aggregates,
            "recent_measurements": recent_measurements,
        },
        "maintenance_history": recent_sessions,
    }
