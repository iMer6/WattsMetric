from typing import Annotated

from fastapi import Depends

from app.core.enums import Permission
from app.core.permissions import MAINTENANCE_READ_ROLES, MAINTENANCE_WRITE_ROLES
from app.sources.dependencies import require_source_access
from app.sources.models import Source

type MaintenanceReadableSource = Annotated[
    Source,
    Depends(
        require_source_access(
            allowed_roles=MAINTENANCE_READ_ROLES,
            required_permissions=(Permission.VIEW_MAINTENANCE_LOGS,),
            include_devices=False,
        )
    ),
]

type MaintenanceWritableSource = Annotated[
    Source,
    Depends(
        require_source_access(
            allowed_roles=MAINTENANCE_WRITE_ROLES,
            required_permissions=(Permission.CREATE_MAINTENANCE_LOGS,),
            include_devices=False,
        )
    ),
]

__all__: list[str] = [
    "MaintenanceReadableSource",
    "MaintenanceWritableSource",
]
