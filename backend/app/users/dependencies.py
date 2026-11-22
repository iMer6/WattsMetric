from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_session
from app.core.enums import Permission, RoleName
from app.users import crud
from app.users.exceptions import (
    InsufficientRolePermissionError,
    InvalidCredentialsError,
    UserDeactivatedError,
    UserNotFoundError,
)
from app.users.models import User

oauth2_scheme: OAuth2PasswordBearer = OAuth2PasswordBearer(
    tokenUrl="/api/v1/users/auth/token"
)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    RoleName.ROOT.value: frozenset(Permission),
    RoleName.ADMIN.value: frozenset({
        Permission.VIEW_SOURCES,
        Permission.VIEW_ANALYTICS,
        Permission.VERIFY_CALCULATIONS,
        Permission.VIEW_ALERTS,
        Permission.MANAGE_ALERTS_RULES,
        Permission.USE_LLM_DIAGNOSTICS,
        Permission.REPLAY_TEST_PROFILES,
        Permission.VIEW_MAINTENANCE_LOGS,
        Permission.CREATE_MAINTENANCE_LOGS,
        Permission.COMMISSION_IOT_NODES,
        Permission.MANAGE_IOT_DEVICES,
        Permission.VIEW_REPORTS,
        Permission.CREATE_REPORTS,
        Permission.VIEW_USERS,
        Permission.CREATE_USERS,
        Permission.MANAGE_USERS,
        Permission.MANAGE_ROLES,
    }),
    RoleName.OWNER.value: frozenset({
        Permission.VIEW_SOURCES,
        Permission.VIEW_ANALYTICS,
        Permission.VIEW_ALERTS,
        Permission.MANAGE_ALERTS_RULES,
        Permission.USE_LLM_DIAGNOSTICS,
        Permission.VIEW_MAINTENANCE_LOGS,
        Permission.MANAGE_IOT_DEVICES,
        Permission.VIEW_REPORTS,
        Permission.CREATE_REPORTS,
    }),
    RoleName.ENGINEER.value: frozenset({
        Permission.VIEW_SOURCES,
        Permission.VIEW_ALERTS,
        Permission.VIEW_MAINTENANCE_LOGS,
        Permission.CREATE_MAINTENANCE_LOGS,
        Permission.COMMISSION_IOT_NODES,
        Permission.MANAGE_IOT_DEVICES,
    }),
    RoleName.DISPATCHER.value: frozenset({
        Permission.VIEW_SOURCES,
        Permission.VIEW_ANALYTICS,
        Permission.VERIFY_CALCULATIONS,
        Permission.VIEW_ALERTS,
        Permission.MANAGE_ALERTS_RULES,
        Permission.USE_LLM_DIAGNOSTICS,
        Permission.REPLAY_TEST_PROFILES,
        Permission.VIEW_MAINTENANCE_LOGS,
        Permission.VIEW_REPORTS,
        Permission.CREATE_REPORTS,
    }),
}


def get_user_permissions(user: User) -> set[Permission]:
    permissions: set[Permission] = set()
    for role in user.roles:
        permissions.update(
            ROLE_PERMISSIONS.get(role.role_name, frozenset())
        )
    return permissions


def user_has_system_role(user: User) -> bool:
    return any(
        role.role_name in {
            RoleName.ROOT.value,
            RoleName.ADMIN.value
        }
        for role in user.roles
    )


async def get_current_active_user(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    try:
        user: User = await crud.get_current_user_from_jwt(db, token=token)
    except (InvalidCredentialsError, UserNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={
                "WWW-Authenticate": "Bearer"
            },
        ) from exc
    except UserDeactivatedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=exc.default_message,
        ) from exc

    if not user.can_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive or has no assigned roles.",
        )
    return user


CurrentUser: type[User] = Annotated[
    User, Depends(get_current_active_user)
]
DbSession: type[AsyncSession] = Annotated[
    AsyncSession, Depends(get_async_session)
]


def require_permissions(
    *required: Permission,
) -> Callable[[CurrentUser], Coroutine[Any, Any, User]]:
    async def _permission_dependency(current_user: CurrentUser) -> User:
        user_perms: set[Permission] = get_user_permissions(current_user)
        has_required_permissions: bool = set(required).issubset(user_perms)
        
        if not has_required_permissions:
            raise InsufficientRolePermissionError(
                "Missing required permissions: "
                + ", ".join(permission.value for permission in required)
            )
        return current_user

    return _permission_dependency
