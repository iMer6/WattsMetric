from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Path

from app.core.enums import Permission, RoleName
from app.core.permissions import (
    ADMIN_ROLES,
    SOURCE_DELETE_ROLES,
    SOURCE_READ_ROLES,
    SOURCE_WRITE_ROLES,
)

from app.sources import crud
from app.sources.models import Source

from app.users.dependencies import (
    CurrentUser,
    DbSession,
    get_user_permissions,
    user_has_system_role,
)
from app.users.exceptions import InsufficientRolePermissionError
from app.users.models import User


def user_has_any_role(
    user: User,
    allowed_roles: frozenset[RoleName]
) -> bool:
    allowed_values: set[str] = {role.value for role in allowed_roles}
    has_any_role: bool = any(
        role.role_name in allowed_values
        for role in user.roles
    )
    return has_any_role


def _ensure_user_permissions(
    user: User,
    required_permissions: tuple[Permission, ...],
) -> None:
    user_perms: set[Permission] = get_user_permissions(user)
    missing_perms: list[str] = [
        perm.value
        for perm in required_permissions
        if perm not in user_perms
    ]
    is_missing_permissions: bool = bool(missing_perms)

    if is_missing_permissions:
        missing_str: str = ", ".join(missing_perms)
        raise InsufficientRolePermissionError(
            f"Missing required permissions: {missing_str}"
        )


async def require_source_creator(current_user: CurrentUser) -> User:
    """
    Verifies that the authenticated user holds permission to create a new
    energy source (`ROOT`, `ADMIN`, or `OWNER` per `SOURCE_WRITE_ROLES`).
    """
    user_perms: set[Permission] = get_user_permissions(current_user)
    is_missing_view_permission: bool = Permission.VIEW_SOURCES not in user_perms
    if is_missing_view_permission:
        raise InsufficientRolePermissionError(
            f"Missing required permission: {Permission.VIEW_SOURCES.value}"
        )

    is_authorized_creator: bool = (
        user_has_any_role(current_user, SOURCE_WRITE_ROLES)
    )
    if not is_authorized_creator:
        raise InsufficientRolePermissionError(
            "Only system administrators or station owners can create energy sources."
        )

    return current_user


def require_source_access(
    *,
    allowed_roles: frozenset[RoleName] = SOURCE_READ_ROLES,
    required_permissions: tuple[Permission, ...] = (Permission.VIEW_SOURCES,),
    include_devices: bool = False,
    include_inactive: bool = False,
) -> Callable[[DbSession, CurrentUser, UUID], Coroutine[Any, Any, Source]]:
    """
    Builds a reusable FastAPI dependency enforcing two-tier RBAC + Object-Level Access Control
    for a specific `source_id` across `sources`, `iot`, `maintenance`, and `telemetry` routers.
    """
    object_role_names: tuple[str, ...] = tuple(
        role.value for role in (allowed_roles - ADMIN_ROLES)
    )

    async def _dependency(
        db: DbSession,
        current_user: CurrentUser,
        source_id: Annotated[
            UUID, Path(description="Target energy source UUID.")
        ],
    ) -> Source:
        _ensure_user_permissions(current_user, required_permissions)

        return await crud.get_source_by_id(
            db,
            source_id,
            actor_id=current_user.user_id,
            allowed_source_roles=object_role_names,
            include_devices=include_devices,
            include_inactive=include_inactive,
        )

    return _dependency


SourceCreatorUser: type[User] = Annotated[
    User,
    Depends(require_source_creator)
]

ReadableSource: type[Source] = Annotated[
    Source,
    Depends(
        require_source_access(
            allowed_roles=SOURCE_READ_ROLES,
            required_permissions=(Permission.VIEW_SOURCES,),
            include_devices=False,
        )
    ),
]

DetailedReadableSource: type[Source] = Annotated[
    Source,
    Depends(
        require_source_access(
            allowed_roles=SOURCE_READ_ROLES,
            required_permissions=(Permission.VIEW_SOURCES,),
            include_devices=True,
        )
    ),
]

WritableSource: type[Source] = Annotated[
    Source,
    Depends(
        require_source_access(
            allowed_roles=SOURCE_WRITE_ROLES,
            required_permissions=(Permission.VIEW_SOURCES,),
            include_devices=False,
        )
    ),
]

DeletableSource: type[Source] = Annotated[
    Source,
    Depends(
        require_source_access(
            allowed_roles=SOURCE_DELETE_ROLES,
            required_permissions=(Permission.VIEW_SOURCES,),
            include_devices=False,
        )
    ),
]

__all__: list[str] = [
    "DeletableSource",
    "DetailedReadableSource",
    "ReadableSource",
    "SourceCreatorUser",
    "WritableSource",
    "require_source_access",
    "require_source_creator",
    "user_has_any_role",
    "user_has_system_role",
]
