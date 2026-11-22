from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.core.enums import Permission, SourceStatus

from app.sources import crud
from app.sources.dependencies import (
    DeletableSource,
    DetailedReadableSource,
    SourceCreatorUser,
    WritableSource,
    user_has_system_role,
)
from app.sources.models import Source
from app.sources.schemas import (
    PaginatedResponse,
    SourceCreateRequest,
    SourceDetailOut,
    SourceShortOut,
    SourceUpdateRequest,
)

from app.users.dependencies import CurrentUser, DbSession, require_permissions
from app.users.models import User

router: APIRouter = APIRouter()


@router.get(
    "",
    tags=["Energy Sources"],
    response_model=PaginatedResponse[SourceShortOut],
    summary="List accessible energy generation sources",
    description=(
        "Returns a paginated list of solar and wind generation facilities accessible to the "
        "authenticated user.\n\n"
        "- **Access Control:** Global `ROOT` and `ADMIN` users view all active facilities; "
        "`OWNER`, `ENGINEER`, and `DISPATCHER` users receive only facilities assigned via "
        "`user_source_access`.\n"
        "- **Required Permission:** `sources:view`."
    ),
)
async def list_sources(
    response: Response,
    db: DbSession,
    current_user: Annotated[
        User, Depends(require_permissions(Permission.VIEW_SOURCES))
    ],
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
        description=(
            "Case-insensitive search by source name, physical address, or QR code."
        ),
    ),
    source_status: SourceStatus | None = Query(
        default=None,
        description=(
            "Filter by operational status (`active`, `inactive`, `maintenance`, `offline`)."
        ),
    ),
) -> PaginatedResponse[SourceShortOut]:
    sources, total = await crud.get_sources(
        db,
        actor_id=current_user.user_id,
        skip=skip,
        limit=limit,
        search=search,
        source_status=source_status,
        include_devices=False,
    )
    response.headers["X-Total-Count"] = str(total)
    return PaginatedResponse[SourceShortOut](
        items=[SourceShortOut.model_validate(s) for s in sources],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "",
    tags=["Energy Sources"],
    response_model=SourceDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new energy generation source",
    description=(
        "Creates a new solar or wind generation station with a unique nameplate QR code.\n\n"
        "- **Automatic Ownership:** If created by a non-administrative `OWNER`, the creator is "
        "atomically granted the `OWNER` role on the new facility in `user_source_access`.\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, `OWNER`."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Conflict — an energy source with this QR code already exists.",
        },
    },
)
async def create_source(
    db: DbSession,
    actor: SourceCreatorUser,
    payload: SourceCreateRequest,
) -> SourceDetailOut:
    resolved_owner_id: UUID | None = (
        payload.owner_user_id
        if user_has_system_role(actor)
        else actor.user_id
    )
    source: Source = await crud.create_source(
        db,
        actor_id=actor.user_id,
        source_name=payload.source_name,
        qr_code=payload.qr_code,
        source_status=payload.source_status,
        source_address=payload.source_address,
        latitude=payload.latitude,
        longitude=payload.longitude,
        owner_user_id=resolved_owner_id,
    )
    return SourceDetailOut.model_validate(source)


@router.get(
    "/by-qr/{qr_code}",
    tags=["Energy Sources"],
    response_model=SourceDetailOut,
    summary="Look up energy source by QR code (Mobile Field App)",
    description=(
        "Resolves an energy generation facility and its attached ESP32 IoT devices by scanning "
        "the station QR code in the Android service engineer application.\n\n"
        "- **Access Control:** Requires `sources:view` and active object-level assignment on the "
        "matched facility (or global `ROOT`/`ADMIN`)."
    ),
)
async def get_source_by_qr(
    db: DbSession,
    current_user: Annotated[
        User, Depends(require_permissions(Permission.VIEW_SOURCES))
    ],
    qr_code: Annotated[
        str,
        Path(
            min_length=3, max_length=255,
            description="Unique QR code string scanned from the station nameplate.",
        ),
    ],
) -> SourceDetailOut:
    source: Source = await crud.get_source_by_qr_code(
        db,
        qr_code=qr_code,
        actor_id=current_user.user_id,
    )
    return SourceDetailOut.model_validate(source)


@router.get(
    "/{source_id}",
    tags=["Energy Sources"],
    response_model=SourceDetailOut,
    summary="Get energy source details with connected IoT nodes",
    description=(
        "Returns full metadata, coordinates, operational status, and preloaded ESP32 IoT "
        "devices for a specific generation facility.\n\n"
        "- **Access Control:** `ROOT`, `ADMIN`, or assigned `OWNER`, `ENGINEER`, `DISPATCHER`."
    ),
)
async def get_source_detail(
    source: DetailedReadableSource,
) -> SourceDetailOut:
    return SourceDetailOut.model_validate(source)


@router.patch(
    "/{source_id}",
    tags=["Energy Sources"],
    response_model=SourceDetailOut,
    summary="Update energy source metadata or status",
    description=(
        "Updates station name, physical address, geographic coordinates, QR code, or "
        "operational `source_status`.\n\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, or `OWNER` assigned to this facility."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Conflict — another energy source already uses this QR code.",
        },
    },
)
async def update_source(
    db: DbSession,
    current_user: CurrentUser,
    source: WritableSource,
    payload: SourceUpdateRequest,
) -> SourceDetailOut:
    updated: Source = await crud.update_source(
        db,
        actor_id=current_user.user_id,
        source_id=source.source_id,
        source_name=payload.source_name,
        source_address=payload.source_address,
        latitude=payload.latitude,
        longitude=payload.longitude,
        qr_code=payload.qr_code,
        source_status=payload.source_status,
    )
    return SourceDetailOut.model_validate(updated)


@router.delete(
    "/{source_id}",
    tags=["Energy Sources"],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive (soft-delete) an energy source",
    description=(
        "Marks an energy generation source as inactive (`is_active=False`), preserving "
        "historical telemetry measurements, time aggregates, and maintenance audit logs.\n\n"
        "- **Allowed Roles:** `ROOT`, `ADMIN`, or `OWNER` assigned to this facility."
    ),
)
async def delete_source(
    db: DbSession,
    current_user: CurrentUser,
    source: DeletableSource,
) -> Response:
    await crud.deactivate_source(
        db,
        actor_id=current_user.user_id,
        source_id=source.source_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
