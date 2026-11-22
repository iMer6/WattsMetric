from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    Path,
    Query,
    Response,
    UploadFile,
    status
)

from app.maintenance import crud
from app.maintenance.dependencies import (
    MaintenanceReadableSource,
    MaintenanceWritableSource,
)
from app.maintenance.models import MaintenanceSession, MaintenancePhoto
from app.maintenance.schemas import (
    MaintenancePhotoOut,
    MaintenanceSessionCreateRequest,
    MaintenanceSessionOut,
    PaginatedResponse,
)
from app.users.dependencies import CurrentUser, DbSession

router: APIRouter = APIRouter()


@router.get(
    "/{source_id}/maintenance-sessions",
    tags=["Maintenance"],
    response_model=PaginatedResponse[MaintenanceSessionOut],
    summary="List maintenance sessions and inspection photos for an energy source",
    description=(
        "Returns the chronological service and inspection log (`maintenance_sessions`) with "
        "assigned service engineers (`maintenance_inspectors`) and attached photographs "
        "(`maintenance_photos`).\n\n"
        "- **Access Control:** `ROOT`, `ADMIN`, or assigned `OWNER`, `ENGINEER`, `DISPATCHER`."
    ),
)
async def list_maintenance_sessions(
    response: Response,
    db: DbSession,
    source: MaintenanceReadableSource,
    skip: int = Query(
        default=0, ge=0,
        description="Number of records to skip."
    ),
    limit: int = Query(
        default=50, ge=1, le=100,
        description="Maximum number of records to return (1–100)."
    ),
) -> PaginatedResponse[MaintenanceSessionOut]:
    sessions, total = await crud.get_maintenance_sessions(
        db,
        source_id=source.source_id,
        skip=skip,
        limit=limit,
    )
    response.headers["X-Total-Count"] = str(total)
    return PaginatedResponse[MaintenanceSessionOut](
        items=[MaintenanceSessionOut.model_validate(s) for s in sessions],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/{source_id}/maintenance-sessions",
    tags=["Maintenance"],
    response_model=MaintenanceSessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create or synchronize an offline field maintenance session",
    description=(
        "Creates or idempotently synchronizes an inspection report recorded by a service "
        "engineer on site via the Android mobile client.\n\n"
        "- **Offline Synchronization:** Accepts a client-generated `session_id` and offline "
        "`created_at` timestamp, marking `is_synced=True` and recording `synced_at`.\n"
        "- **Allowed Roles:** `ENGINEER` assigned to this facility (or global `ROOT`/`ADMIN`)."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Conflict — maintenance session ID belongs to a different source.",
        },
    },
)
async def create_maintenance_session(
    db: DbSession,
    current_user: CurrentUser,
    source: MaintenanceWritableSource,
    payload: MaintenanceSessionCreateRequest,
) -> MaintenanceSessionOut:
    session: MaintenanceSession = await crud.create_or_sync_maintenance_session(
        db,
        source_id=source.source_id,
        actor_id=current_user.user_id,
        inspector_notes=payload.inspector_notes,
        session_id=payload.session_id,
        created_at=payload.created_at,
        inspector_ids=payload.inspector_ids,
    )
    return MaintenanceSessionOut.model_validate(session)


@router.post(
    "/{source_id}/maintenance-sessions/{session_id}/photos",
    tags=["Maintenance"],
    response_model=MaintenancePhotoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload inspection photograph to a maintenance session",
    description=(
        "Uploads and links a panel/equipment condition photograph (`JPEG`, `PNG`, or `WebP`, up "
        "to `settings.MAX_PHOTO_SIZE_MB`) to an existing maintenance session.\n\n"
        "- **Security:** Validates binary file magic bytes, enforces size caps, verifies that "
        "`session_id` belongs to `source_id`, and stores files under UUID names to prevent Path "
        "Traversal.\n"
        "- **Allowed Roles:** `ENGINEER` assigned to this facility (or global `ROOT`/`ADMIN`)."
    ),
    responses={
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {
            "description": "Payload Too Large — uploaded photo exceeds the max allowed size.",
        },
    },
)
async def upload_session_photo(
    db: DbSession,
    source: MaintenanceWritableSource,
    session_id: Annotated[
        UUID,
        Path(description="Target maintenance session UUID.")
    ],
    file: Annotated[
        UploadFile,
        File(description="Inspection image file (JPEG, PNG, or WebP)."),
    ],
    created_at: Annotated[
        datetime | None,
        Form(
            description=(
                "Optional UTC timestamp when the photograph was captured offline."
            )
        ),
    ] = None,
) -> MaintenancePhotoOut:
    photo: MaintenancePhoto = await crud.upload_maintenance_photo(
        db,
        source_id=source.source_id,
        session_id=session_id,
        file=file,
        created_at=created_at,
    )
    return MaintenancePhotoOut.model_validate(photo)
