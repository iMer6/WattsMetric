from collections.abc import Callable, Coroutine
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import Depends, Path

from app.core.enums import Permission, RoleName
from app.core.permissions import IOT_DEVICE_MANAGE_ROLES, IOT_DEVICE_READ_ROLES
from app.iot import crud
from app.iot.models import IoTDevice
from app.sources.dependencies import require_source_access
from app.sources.models import Source
from app.users.dependencies import CurrentUser, DbSession


def require_source_device_access(
    *,
    allowed_roles: frozenset[RoleName] = IOT_DEVICE_READ_ROLES,
    required_permissions: tuple[Permission, ...] = (Permission.VIEW_SOURCES,),
) -> Callable[
    [DbSession, CurrentUser, UUID, UUID],
    Coroutine[Any, Any, tuple[Source, IoTDevice]],
]:
    # noinspection SpellCheckingInspection
    """
    Enforces IDOR/BOLA protection across nested IoT devices: verifies both
    object-level permissions on `source_id` and that `iot_id` belongs to `source_id`.
    """
    source_dep = require_source_access(
        allowed_roles=allowed_roles,
        required_permissions=required_permissions,
        include_devices=False,
        include_inactive=False,
    )

    async def _dependency(
        db: DbSession,
        current_user: CurrentUser,
        source_id: Annotated[
            UUID, Path(description="Parent energy source UUID.")
        ],
        iot_id: Annotated[
            UUID, Path(description="Target IoT device UUID.")
        ],
    ) -> tuple[Source, IoTDevice]:
        source: Source = await source_dep(db, current_user, source_id)
        device: IoTDevice = await crud.get_iot_device_by_id(
            db,
            source_id=cast(UUID, source.source_id),
            iot_id=iot_id,
        )
        return source, device

    return _dependency


DeviceManageableSource: type[Source] = Annotated[
    Source,
    Depends(
        require_source_access(
            allowed_roles=IOT_DEVICE_MANAGE_ROLES,
            required_permissions=(Permission.MANAGE_IOT_DEVICES,),
            include_devices=False,
        )
    ),
]

ReadableSourceDevice: type[tuple[Source, IoTDevice]] = Annotated[
    tuple[Source, IoTDevice],
    Depends(
        require_source_device_access(
            allowed_roles=IOT_DEVICE_READ_ROLES,
            required_permissions=(Permission.VIEW_SOURCES,),
        )
    ),
]

ManageableSourceDevice: type[tuple[Source, IoTDevice]] = Annotated[
    tuple[Source, IoTDevice],
    Depends(
        require_source_device_access(
            allowed_roles=IOT_DEVICE_MANAGE_ROLES,
            required_permissions=(Permission.MANAGE_IOT_DEVICES,),
        )
    ),
]

__all__: list[str] = [
    "DeviceManageableSource",
    "ManageableSourceDevice",
    "ReadableSourceDevice",
    "require_source_device_access",
]
