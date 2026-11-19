from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import RoleName, SourceStatus
from app.core.utils import escape_like

from app.sources.exceptions import *
from app.sources.models import Source

from app.users.models import Role, User, UserRole, UserSourceAccess

SYSTEM_ROLE_NAMES: frozenset[str] = frozenset({
    RoleName.ROOT.value,
    RoleName.ADMIN.value,
})


def _validate_coordinates(
    latitude: Decimal | None,
    longitude: Decimal | None,
) -> None:
    is_invalid_latitude: bool = latitude is not None and not (
        Decimal("-90") <= latitude <= Decimal("90")
    )
    if is_invalid_latitude:
        raise InvalidSourceCoordinatesError(
            "Latitude must be between -90 and 90 degrees."
        )

    is_invalid_longitude: bool = longitude is not None and not (
        Decimal("-180") <= longitude <= Decimal("180")
    )
    if is_invalid_longitude:
        raise InvalidSourceCoordinatesError(
            "Longitude must be between -180 and 180 degrees."
        )


async def _user_has_system_role(
    db: AsyncSession,
    user_id: UUID,
) -> bool:
    role_exists_stmt = select(
        exists().where(
            and_(
                UserRole.user_id == user_id,
                UserRole.role_id == Role.role_id,
                Role.role_name.in_(SYSTEM_ROLE_NAMES),
            )
        )
    )
    has_system_role: bool | None = await db.scalar(role_exists_stmt)
    return bool(has_system_role)


async def ensure_user_source_permission(
    db: AsyncSession,
    *,
    user_id: UUID,
    source_id: UUID,
    allowed_source_roles: Sequence[str] | None = None,
) -> None:
    # noinspection SpellCheckingInspection
    """
    Verifies a user's access permissions to a specific generation source (IDOR protection).
    Users with global ROOT or ADMIN roles are granted full access.
    If `allowed_source_roles` is not specified, any valid record in `user_source_access` suffices.
    """
    is_system_admin: bool = await _user_has_system_role(db, user_id)
    if is_system_admin:
        return

    conditions: list = [
        UserSourceAccess.user_id == user_id,
        UserSourceAccess.source_id == source_id,
        User.user_id == user_id,
        User.is_active.is_(True),
    ]
    has_role_restrictions: bool = bool(allowed_source_roles)
    if has_role_restrictions:
        conditions.extend([
            UserSourceAccess.role_id == Role.role_id,
            Role.role_name.in_(tuple(allowed_source_roles)),  # type: ignore[arg-type]
        ])

    access_stmt = select(exists().where(and_(*conditions)))
    has_access: bool | None = await db.scalar(access_stmt)
    if not has_access:
        raise SourceAccessDeniedError(
            f"User {user_id} is not authorized for source {source_id}."
        )


async def create_source(
    db: AsyncSession,
    *,
    actor_id: UUID,
    source_name: str,
    qr_code: str,
    source_status: SourceStatus = SourceStatus.ACTIVE,
    source_address: str | None = None,
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
    owner_user_id: UUID | None = None,
) -> Source:
    """
    Registers a new generation source (solar/wind power plant) as active in the system.
    If `owner_user_id` is provided, atomically assigns the OWNER role to that user
    in the `user_source_access` table.
    """
    _validate_coordinates(latitude, longitude)
    cleaned_qr: str = qr_code.strip()

    existing_qr_stmt = select(
        exists().where(Source.qr_code == cleaned_qr)
    )
    qr_already_exists: bool | None = await db.scalar(existing_qr_stmt)
    if qr_already_exists:
        raise SourceQrCodeAlreadyExistsError(
            f"Energy source with QR code '{cleaned_qr}' already exists."
        )

    owner_role_id: int | None = None
    has_owner: bool = owner_user_id is not None
    if has_owner:
        owner_role_stmt = select(Role.role_id).where(
            Role.role_name == RoleName.OWNER.value
        )
        owner_role_id: int | None = await db.scalar(owner_role_stmt)

    new_source: Source = Source(
        source_name=source_name.strip(),
        source_address=source_address.strip() if source_address else None,
        latitude=latitude,
        longitude=longitude,
        qr_code=cleaned_qr,
        source_status=source_status,
        is_active=True,
    )

    try:
        db.add(new_source)
        await db.flush()

        can_assign_owner: bool = (
            owner_user_id is not None
            and owner_role_id is not None
        )
        if can_assign_owner:
            user_access: UserSourceAccess = UserSourceAccess(
                user_id=owner_user_id,
                source_id=new_source.source_id,
                role_id=owner_role_id,
            )
            db.add(user_access)

        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        is_qr_conflict: bool = "qr_code" in str(exc.orig).lower()
        if is_qr_conflict:
            raise SourceQrCodeAlreadyExistsError(
                f"Energy source with QR code '{cleaned_qr}' already exists."
            ) from exc
        raise FailedToCreateSourceError() from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToCreateSourceError() from exc

    return await get_source_by_id(
        db,
        new_source.source_id,
        actor_id=actor_id,
        include_devices=True,
    )


async def get_source_by_id(
    db: AsyncSession,
    source_id: UUID,
    *,
    actor_id: UUID,
    include_devices: bool = False,
    include_inactive: bool = False,
) -> Source:
    """
    Retrieves a generation source by UUID with optional IoT node preloading
    and user permission verification. Excludes soft-deleted (`is_active=False`) sources by default.
    """
    conditions: list = [Source.source_id == source_id]
    only_active: bool = not include_inactive
    if only_active:
        conditions.append(Source.is_active.is_(True))

    source_stmt = select(Source).where(and_(*conditions))
    if include_devices:
        source_stmt = source_stmt.options(selectinload(Source.iot_devices))

    source: Source | None = await db.scalar(source_stmt)
    if source is None:
        raise SourceNotFoundError(f"Source {source_id} not found.")

    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=source_id,
    )

    return source


async def get_source_by_qr_code(
    db: AsyncSession,
    qr_code: str,
    *,
    actor_id: UUID,
    include_inactive: bool = False,
) -> Source:
    """
    Quickly looks up a station by its QR code (intended for the field service engineer mobile app),
    preloading attached ESP32 IoT devices. Excludes soft-deleted (`is_active=False`) sources by default.
    """
    cleaned_qr: str = qr_code.strip()
    conditions: list = [Source.qr_code == cleaned_qr]
    only_active: bool = not include_inactive
    if only_active:
        conditions.append(Source.is_active.is_(True))

    source_stmt = (
        select(Source)
        .where(and_(*conditions))
        .options(selectinload(Source.iot_devices))
    )
    source: Source | None = await db.scalar(source_stmt)
    if source is None:
        raise SourceNotFoundError(
            f"Source with QR code '{cleaned_qr}' not found."
        )

    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=source.source_id,
    )

    return source


async def get_sources(
    db: AsyncSession,
    *,
    actor_id: UUID,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    source_status: SourceStatus | None = None,
    include_devices: bool = False,
    include_inactive: bool = False,
) -> tuple[list[Source], int]:
    """
    Returns a paginated list of generation sources and their total count.
    If the actor is not a ROOT/ADMIN, automatically filters the results
    to only those sources accessible via `user_source_access`.
    Soft-deleted (`is_active=False`) sources are excluded unless `include_inactive=True`.
    """
    filters: list[Any] = []

    only_active: bool = not include_inactive
    if only_active:
        filters.append(Source.is_active.is_(True))

    is_system_admin: bool = await _user_has_system_role(db, actor_id)
    if not is_system_admin:
        user_access_exists = exists().where(
            and_(
                UserSourceAccess.source_id == Source.source_id,
                UserSourceAccess.user_id == actor_id,
            )
        )
        filters.append(user_access_exists)

    has_search_query: bool = bool(search and search.strip())
    if has_search_query:
        escaped: str = f"%{escape_like(search.strip())}%"  # type: ignore[union-attr]
        search_condition = or_(
            Source.source_name.ilike(escaped, escape="\\"),
            Source.source_address.ilike(escaped, escape="\\"),
            Source.qr_code.ilike(escaped, escape="\\"),
        )
        filters.append(search_condition)

    has_status_filter: bool = source_status is not None
    if has_status_filter:
        filters.append(Source.source_status == source_status)

    count_stmt = select(func.count(Source.source_id)).where(*filters)
    raw_total = await db.scalar(count_stmt)
    total: int = int(raw_total or 0)
    if total == 0:
        return [], 0

    sources_stmt = (
        select(Source)
        .where(*filters)
        .order_by(
            Source.created_at.desc(),
            Source.source_id.asc(),
        )
        .offset(skip)
        .limit(limit)
    )
    if include_devices:
        sources_stmt = sources_stmt.options(selectinload(Source.iot_devices))

    sources_result = await db.scalars(sources_stmt)
    sources: list[Source] = list(sources_result.all())
    return sources, total


async def get_user_accessible_sources(
    db: AsyncSession,
    user_id: UUID,
    *,
    include_devices: bool = True,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """
    Retrieves a list of all stations accessible to a specific user,
    along with their role name on each source.
    For ROOT/ADMIN users, returns all system stations with the corresponding administrative role.
    Excludes soft-deleted (`is_active=False`) sources unless `include_inactive=True`.
    """
    active_filter: list = [] if include_inactive else [Source.is_active.is_(True)]
    is_system_admin: bool = await _user_has_system_role(db, user_id)

    if is_system_admin:
        all_sources_stmt = (
            select(Source)
            .where(*active_filter)
            .order_by(Source.created_at.desc())
        )
        if include_devices:
            all_sources_stmt = all_sources_stmt.options(
                selectinload(Source.iot_devices)
            )

        all_sources_result = await db.scalars(all_sources_stmt)
        all_sources: list[Source] = list(all_sources_result.all())
        return [
            {
                "source": source,
                "role_name": RoleName.ADMIN.value,
            }
            for source in all_sources
        ]

    user_sources_stmt = (
        select(
            Source,
            Role.role_name,
        )
        .join(
            UserSourceAccess,
            UserSourceAccess.source_id == Source.source_id,
        )
        .join(
            Role,
            Role.role_id == UserSourceAccess.role_id,
        )
        .where(
            UserSourceAccess.user_id == user_id,
            *active_filter,
        )
        .order_by(Source.created_at.desc())
    )
    if include_devices:
        user_sources_stmt = user_sources_stmt.options(
            selectinload(Source.iot_devices)
        )

    rows_result = await db.execute(user_sources_stmt)
    rows = rows_result.all()
    return [
        {
            "source": row[0],
            "role_name": row[1],
        }
        for row in rows
    ]


async def update_source(
    db: AsyncSession,
    *,
    actor_id: UUID,
    source_id: UUID,
    source_name: str | None = None,
    source_address: str | None = None,
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
    qr_code: str | None = None,
    source_status: SourceStatus | None = None,
    is_active: bool | None = None,
) -> Source:
    """
    Updates the metadata, coordinates, QR code, physical status, or system active state
    of a generation source.
    Restricted to the source owner (OWNER) or system administrators.
    """
    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=source_id,
        allowed_source_roles=(RoleName.OWNER.value,),
    )

    _validate_coordinates(latitude, longitude)

    conditions: list = [Source.source_id == source_id]
    is_not_reactivating: bool = is_active is not True
    if is_not_reactivating:
        conditions.append(Source.is_active.is_(True))

    source_stmt = (
        select(Source)
        .where(and_(*conditions))
        .with_for_update()
    )
    source: Source | None = await db.scalar(source_stmt)
    if source is None:
        raise SourceNotFoundError(f"Source {source_id} not found.")

    has_qr_update: bool = qr_code is not None
    if has_qr_update:
        cleaned_qr: str = qr_code.strip()  # type: ignore[union-attr]
        is_qr_changed: bool = cleaned_qr != source.qr_code
        if is_qr_changed:
            qr_exists_stmt = select(
                exists().where(
                    and_(
                        Source.qr_code == cleaned_qr,
                        Source.source_id != source_id,
                    )
                )
            )
            qr_exists: bool | None = await db.scalar(qr_exists_stmt)
            if qr_exists:
                raise SourceQrCodeAlreadyExistsError(
                    f"Energy source with QR code '{cleaned_qr}' already exists."
                )
            source.qr_code = cleaned_qr

    if source_name is not None:
        source.source_name = source_name.strip()
    if source_address is not None:
        source.source_address = source_address.strip() or None
    if latitude is not None:
        source.latitude = latitude
    if longitude is not None:
        source.longitude = longitude
    if source_status is not None:
        source.source_status = source_status
    if is_active is not None:
        source.is_active = is_active

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        is_qr_conflict: bool = "qr_code" in str(exc.orig).lower()
        if is_qr_conflict:
            raise SourceQrCodeAlreadyExistsError() from exc
        raise FailedToUpdateSourceError() from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateSourceError() from exc

    return await get_source_by_id(
        db,
        source_id,
        actor_id=actor_id,
        include_devices=True,
        include_inactive=True,
    )


async def deactivate_source(
    db: AsyncSession,
    *,
    actor_id: UUID,
    source_id: UUID,
) -> None:
    """
    Soft-deletes a generation source by setting `is_active` to False,
    preserving its physical `source_status`, associated devices, telemetry, and historical sessions.
    """
    await ensure_user_source_permission(
        db,
        user_id=actor_id,
        source_id=source_id,
        allowed_source_roles=(RoleName.OWNER.value,),
    )

    source_stmt = (
        select(Source)
        .where(
            and_(
                Source.source_id == source_id,
                Source.is_active.is_(True),
            )
        )
        .with_for_update()
    )
    source: Source | None = await db.scalar(source_stmt)
    if source is None:
        raise SourceNotFoundError(
            f"Source {source_id} not found or already inactive."
        )

    source.is_active = False

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToDeleteSourceError() from exc
