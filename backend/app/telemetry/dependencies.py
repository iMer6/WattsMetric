from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.enums import Permission, RoleName
from app.core.permissions import ADMIN_ROLES, TEST_PROFILE_MANAGE_ROLES
from app.iot.dependencies import ManageableSourceDevice, ReadableSourceDevice
from app.sources.dependencies import require_source_access, user_has_any_role
from app.sources.models import Source
from app.users.dependencies import CurrentUser, get_user_permissions
from app.users.exceptions import InsufficientRolePermissionError
from app.users.models import User


async def require_test_profile_manager(
    current_user: CurrentUser,
) -> User:
    """
    Verifies access to calibration and replay test profiles.

    Matches `Permission.REPLAY_TEST_PROFILES` and `TEST_PROFILE_MANAGE_ROLES`
    (`ROOT`, `ADMIN`, `OWNER`, `DISPATCHER`) from `app/core/permissions.py`.
    """
    user_perms: set[Permission] = get_user_permissions(current_user)
    has_permission: bool = Permission.REPLAY_TEST_PROFILES in user_perms
    has_allowed_role: bool = user_has_any_role(
        current_user, TEST_PROFILE_MANAGE_ROLES
    )

    if not (has_permission or has_allowed_role):
        raise InsufficientRolePermissionError(
            "Insufficient permissions to manage or replay test profiles."
        )
    return current_user


TestProfileManagerUser: type[User] = Annotated[
    User,
    Depends(require_test_profile_manager)
]

AnalyticsReadableSource: type[Source] = Annotated[
    Source,
    Depends(
        require_source_access(
            allowed_roles=ADMIN_ROLES | {RoleName.OWNER, RoleName.DISPATCHER},
            required_permissions=(Permission.VIEW_ANALYTICS,),
            include_devices=False,
        )
    ),
]

__all__: list[str] = [
    "AnalyticsReadableSource",
    "ManageableSourceDevice",
    "ReadableSourceDevice",
    "TestProfileManagerUser",
    "require_test_profile_manager",
]
