from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import and_, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import RoleName

from app.maintenance.exceptions import (
    FailedToSyncMaintenanceError,
    InspectorNotFoundError,
    InvalidInspectorAssignmentError,
    InvalidMaintenanceTimestampError,
    InvalidMaintenanceUploadError,
    MaintenanceSessionConflictError,
    MaintenanceSessionNotFoundError,
    PhotoFileTooLargeError,
)
from app.maintenance.models import (
    MaintenanceInspector,
    MaintenancePhoto,
    MaintenanceSession,
)
from app.users.models import Role, User, UserSourceAccess

SYSTEM_ROLE_NAMES: frozenset[str] = frozenset({
    RoleName.ROOT.value,
    RoleName.ADMIN.value,
})

_ALLOWED_IMAGE_SIGNATURES: dict[str, tuple[str, str]] = {
    "jpeg": (".jpg", "image/jpeg"),
    "png": (".png", "image/png"),
    "webp": (".webp", "image/webp"),
}

_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "application/octet-stream",
})


def _detect_image_type(header: bytes) -> tuple[str, str] | None:
    """
    Identifies image extension and canonical MIME type via magic bytes.
    """
    is_jpeg: bool = header.startswith(b"\xff\xd8\xff")
    if is_jpeg:
        return _ALLOWED_IMAGE_SIGNATURES["jpeg"]

    is_png: bool = header.startswith(b"\x89PNG\r\n\x1a\n")
    if is_png:
        return _ALLOWED_IMAGE_SIGNATURES["png"]

    is_webp: bool = (
        len(header) >= 12
        and header[:4] == b"RIFF"
        and header[8:12] == b"WEBP"
    )
    if is_webp:
        return _ALLOWED_IMAGE_SIGNATURES["webp"]

    return None


async def _validate_maintenance_inspectors(
    db: AsyncSession,
    *,
    source_id: UUID,
    inspector_ids: Sequence[UUID],
) -> None:
    """
    Validates that every specified inspector exists (`InspectorNotFoundError`),
    is active, and either holds a global admin role or holds the `ENGINEER` role
    assigned to `source_id` (`InvalidInspectorAssignmentError`).
    """
    users_stmt = (
        select(User)
        .where(User.user_id.in_(inspector_ids))
        .options(selectinload(User.roles))
    )
    users: list[User] = list((await db.scalars(users_stmt)).all())

    all_inspectors_exist: bool = len(users) == len(inspector_ids)
    if not all_inspectors_exist:
        raise InspectorNotFoundError(
            "One or more specified service inspectors do not exist."
        )

    for user in users:
        is_user_active: bool = bool(user.is_active)
        if not is_user_active:
            raise InvalidInspectorAssignmentError(
                f"Inspector {user.user_id} is deactivated."
            )

        is_admin: bool = any(
            r.role_name in SYSTEM_ROLE_NAMES for r in user.roles
        )
        if is_admin:
            continue

        engineer_access_stmt = select(
            exists().where(
                and_(
                    UserSourceAccess.user_id == user.user_id,
                    UserSourceAccess.source_id == source_id,
                    UserSourceAccess.role_id == Role.role_id,
                    Role.role_name == RoleName.ENGINEER.value,
                )
            )
        )
        has_engineer_access: bool = bool(await db.scalar(engineer_access_stmt))
        if not has_engineer_access:
            raise InvalidInspectorAssignmentError(
                f"User {user.user_id} lacks the ENGINEER role on energy source {source_id}."
            )


async def get_maintenance_sessions(
    db: AsyncSession,
    *,
    source_id: UUID,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[MaintenanceSession], int]:
    """
    Retrieves paginated maintenance sessions for a source with eager loading of
    assigned inspectors and attached photos (prevents N+1 queries).
    """
    count_stmt = (
        select(func.count(MaintenanceSession.session_id))
        .where(MaintenanceSession.source_id == source_id)
    )
    raw_total: int | None = await db.scalar(count_stmt)
    total: int = int(raw_total or 0)

    has_sessions: bool = total > 0
    if not has_sessions:
        return [], 0

    sessions_stmt = (
        select(MaintenanceSession)
        .where(MaintenanceSession.source_id == source_id)
        .options(
            selectinload(MaintenanceSession.inspectors),
            selectinload(MaintenanceSession.photos),
        )
        .order_by(
            MaintenanceSession.created_at.desc(),
            MaintenanceSession.session_id.asc(),
        )
        .offset(skip)
        .limit(limit)
    )
    result = await db.scalars(sessions_stmt)
    return list(result.all()), total


async def create_or_sync_maintenance_session(
    db: AsyncSession,
    *,
    source_id: UUID,
    actor_id: UUID,
    inspector_notes: str,
    session_id: UUID | None = None,
    created_at: datetime | None = None,
    inspector_ids: Sequence[UUID] = (),
) -> MaintenanceSession:
    """
    Creates or idempotently synchronizes an offline field maintenance session.

    - Automatically links `actor_id` plus any additional valid `inspector_ids` in
          `maintenance_inspectors`.
    - If `session_id` already exists on the same `source_id`, updates notes and sync
          timestamp idempotently; if it belongs to another source, raises
          `MaintenanceSessionConflictError`.
    """
    now_utc: datetime = datetime.now(timezone.utc)
    has_custom_created_at: bool = created_at is not None
    
    if has_custom_created_at:
        assert created_at is not None
        is_timezone_aware: bool = (
            created_at.tzinfo is not None
            and created_at.tzinfo.utcoffset(created_at) is not None
        )
        
        if not is_timezone_aware:
            raise InvalidMaintenanceTimestampError()
        
        effective_created_at: datetime = created_at.astimezone(timezone.utc)
    else:
        effective_created_at = now_utc

    unique_inspector_ids: list[UUID] = list(
        dict.fromkeys([actor_id, *inspector_ids])
    )
    
    await _validate_maintenance_inspectors(
        db,
        source_id=source_id,
        inspector_ids=unique_inspector_ids,
    )

    target_session_id: UUID = session_id or uuid.uuid4()

    try:
        existing_stmt = (
            select(MaintenanceSession)
            .where(MaintenanceSession.session_id == target_session_id)
            .with_for_update()
        )
        existing_session: MaintenanceSession | None = await db.scalar(existing_stmt)

        session_already_exists: bool = existing_session is not None
        
        if session_already_exists:
            assert existing_session is not None
            belongs_to_same_source: bool = existing_session.source_id == source_id
            
            if not belongs_to_same_source:
                raise MaintenanceSessionConflictError(
                    f"Maintenance session {target_session_id} belongs to another energy source."
                )
            
            existing_session.inspector_notes = inspector_notes.strip()
            existing_session.is_synced = True
            existing_session.synced_at = now_utc
        else:
            new_session: MaintenanceSession = MaintenanceSession(
                session_id=target_session_id,
                source_id=source_id,
                inspector_notes=inspector_notes.strip(),
                is_synced=True,
                created_at=effective_created_at,
                synced_at=now_utc,
            )
            db.add(new_session)
            await db.flush()

        inspector_rows: list[dict[str, UUID]] = [
            {
                "session_id": target_session_id,
                "inspector_id": uid,
            }
            for uid in unique_inspector_ids
        ]
        inspector_insert = (
            pg_insert(MaintenanceInspector)
            .values(inspector_rows)
            .on_conflict_do_nothing(
                index_elements=["session_id", "inspector_id"]
            )
        )
        await db.execute(inspector_insert)
        await db.commit()
    except MaintenanceSessionConflictError:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToSyncMaintenanceError() from exc

    reloaded_stmt = (
        select(MaintenanceSession)
        .where(MaintenanceSession.session_id == target_session_id)
        .options(
            selectinload(MaintenanceSession.inspectors),
            selectinload(MaintenanceSession.photos),
        )
    )
    reloaded: MaintenanceSession | None = await db.scalar(reloaded_stmt)
    is_session_reloaded: bool = reloaded is not None
    
    if not is_session_reloaded:
        raise FailedToSyncMaintenanceError()
    assert reloaded is not None
    return reloaded


async def upload_maintenance_photo(
    db: AsyncSession,
    *,
    source_id: UUID,
    session_id: UUID,
    file: UploadFile,
    created_at: datetime | None = None,
) -> MaintenancePhoto:
    """
    Validates, securely stores, and registers a field inspection photograph.

    Security controls:
    - Verifies `session_id` belongs to `source_id` (BOLA protection).
    - Streams upload chunks up to `settings.MAX_PHOTO_SIZE_MB` (HTTP 413 if exceeded).
    - Verifies image magic bytes (JPEG, PNG, WebP) independent of client headers.
    - Stores file under a server-generated UUID filename inside `settings.UPLOAD_DIR`
          to eliminate Path Traversal vulnerabilities.
    """
    now_utc: datetime = datetime.now(timezone.utc)
    has_custom_created_at: bool = created_at is not None
    
    if has_custom_created_at:
        assert created_at is not None
        is_timezone_aware: bool = (
            created_at.tzinfo is not None
            and created_at.tzinfo.utcoffset(created_at) is not None
        )
        
        if not is_timezone_aware:
            raise InvalidMaintenanceTimestampError()
        
        effective_created_at: datetime = created_at.astimezone(timezone.utc)
    else:
        effective_created_at = now_utc

    session_stmt = (
        select(MaintenanceSession)
        .where(
            and_(
                MaintenanceSession.session_id == session_id,
                MaintenanceSession.source_id == source_id,
            )
        )
    )
    session: MaintenanceSession | None = await db.scalar(session_stmt)
    session_exists: bool = session is not None
    
    if not session_exists:
        raise MaintenanceSessionNotFoundError(
            f"Maintenance session {session_id} was not found on energy source {source_id}."
        )

    max_photo_mb: int = int(getattr(settings, "MAX_PHOTO_SIZE_MB", 10))
    max_bytes: int = max_photo_mb * 1024 * 1024

    chunks: list[bytes] = []
    total_read: int = 0
    while True:
        chunk: bytes = await file.read(65536)
        has_chunk_data: bool = bool(chunk)
        
        if not has_chunk_data:
            break

        total_read += len(chunk)
        is_within_size_limit: bool = total_read <= max_bytes
        
        if not is_within_size_limit:
            raise PhotoFileTooLargeError(
                f"Photo size exceeds the max allowed limit of {max_photo_mb} MB."
            )
        chunks.append(chunk)

    file_bytes: bytes = b"".join(chunks)
    has_file_content: bool = bool(file_bytes)
    
    if not has_file_content:
        raise InvalidMaintenanceUploadError("Uploaded photo file is empty.")

    detected: tuple[str, str] | None = _detect_image_type(file_bytes[:16])
    is_supported_image: bool = detected is not None
    
    if not is_supported_image:
        raise InvalidMaintenanceUploadError(
            "Unsupported or corrupted image format. "
            "Only JPEG, PNG, and WebP are accepted."
        )

    assert detected is not None
    extension, canonical_mime = detected

    has_valid_content_type: bool = (
        file.content_type is None
        or file.content_type.lower() in _ALLOWED_CONTENT_TYPES
    )
    if not has_valid_content_type:
        raise InvalidMaintenanceUploadError(
            f"Invalid Content-Type '{file.content_type}' for image upload ({canonical_mime})."
        )

    photo_id: UUID = uuid.uuid4()
    safe_filename: str = f"{photo_id}{extension}"

    base_upload_dir: Path = Path(
        getattr(settings, "UPLOAD_DIR", "uploads")
    ).resolve()
    
    target_dir: Path = (
        base_upload_dir / "maintenance" / str(source_id) / str(session_id)
    ).resolve()

    is_within_storage_root: bool = base_upload_dir in target_dir.parents
    if not is_within_storage_root:
        raise InvalidMaintenanceUploadError("Resolved upload path escapes storage root.")

    target_file_path: Path = target_dir / safe_filename

    def _write_file_sync() -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file_path.write_bytes(file_bytes)

    await asyncio.to_thread(_write_file_sync)
    
    upload_base_url: str = getattr(
        settings, "UPLOAD_URL", "/media/photos"
    ).rstrip("/")
    relative_url: str = (
        f"{upload_base_url}/maintenance/{source_id}/{session_id}/{safe_filename}"
    )

    photo: MaintenancePhoto = MaintenancePhoto(
        photo_id=photo_id,
        session_id=session_id,
        photo_url=relative_url,
        created_at=effective_created_at,
        uploaded_at=now_utc,
    )

    try:
        db.add(photo)
        await db.commit()
        await db.refresh(photo)
    except SQLAlchemyError as exc:
        await db.rollback()
        await asyncio.to_thread(target_file_path.unlink, missing_ok=True)
        raise FailedToSyncMaintenanceError() from exc

    return photo
