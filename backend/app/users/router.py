from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm

from app.core.enums import Permission, RoleName
from app.users import crud
from app.users.dependencies import (
    CurrentUser,
    DbSession,
    get_user_permissions,
    require_permissions,
)
from app.users.exceptions import (
    InsufficientRolePermissionError,
    InvalidCredentialsError,
    UserDeactivatedError,
)
from app.users.models import User
from app.users.schemas import (
    AssignInspectorRequest,
    PaginatedUsersResponse,
    PasswordChangeRequest,
    RoleOut,
    SourceAccessCreateRequest,
    SourceAccessUpdateRequest,
    TokenResponse,
    UserAdminCreateRequest,
    UserDetailOut,
    UserOut,
    UserRegisterRequest,
    UserRoleAssignRequest,
    UserSourceAccessOut,
    UserUpdateRequest,
)

router: APIRouter = APIRouter()


# Authentication & Registration


@router.post(
    "/auth/token",
    response_model=TokenResponse,
    summary="OAuth 2.0 Password Flow login",
)
async def login_for_access_token(
    db: DbSession,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    """
    Authentication using the OAuth 2.0 Password Flow standard.
    
    The `username` field is used to pass the `user_email`.
    
    Returns a generic 401 error to protect against user enumeration.
    """
    try:
        token_data: dict[str, str | int] = await crud.authenticate_user_oauth2(
            db,
            email=form_data.username,
            password=form_data.password,
        )
    except (InvalidCredentialsError, UserDeactivatedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password, or account is inactive.",
            headers={
                "WWW-Authenticate": "Bearer"
            },
        ) from exc

    return TokenResponse(**token_data)


@router.post(
    "/auth/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Self-registration for energy source owners",
)
async def register_owner(
    db: DbSession,
    payload: UserRegisterRequest,
) -> User:
    """
    Public registration of a generation system owner (B2B / B2C).
    
    The role is strictly fixed as `OWNER` to prevent privilege escalation.
    """
    return await crud.create_user(
        db,
        user_name=payload.user_name,
        user_email=str(payload.user_email),
        password=payload.password,
        initial_role_names=(RoleName.OWNER.value,),
    )


# Current User Profile (/me)


@router.get(
    "/me",
    response_model=UserDetailOut,
    summary="Get current authenticated user profile",
)
async def get_my_profile(
    db: DbSession,
    current_user: CurrentUser,
) -> User:
    return await crud.get_user_by_id(
        db,
        current_user.user_id,
        include_accesses=True,
    )


@router.patch(
    "/me",
    response_model=UserOut,
    summary="Update current user profile",
)
async def update_my_profile(
    db: DbSession,
    current_user: CurrentUser,
    payload: UserUpdateRequest,
) -> User:
    return await crud.update_user(
        db,
        actor_id=current_user.user_id,
        target_user_id=current_user.user_id,
        user_name=payload.user_name,
        user_email=str(payload.user_email) if payload.user_email else None,
    )


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change current user password",
)
async def change_my_password(
    db: DbSession,
    current_user: CurrentUser,
    payload: PasswordChangeRequest,
) -> Response:
    await crud.change_user_password(
        db,
        user_id=current_user.user_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Roles Reference


@router.get(
    "/roles",
    response_model=list[RoleOut],
    summary="List all available system and object roles",
)
async def list_roles(
    db: DbSession,
    _: CurrentUser,
) -> list:
    return await crud.get_all_roles(db)


# User Administration


@router.get(
    "",
    response_model=PaginatedUsersResponse,
    summary="Search and paginate users",
)
async def list_users(
    response: Response,
    db: DbSession,
    _: Annotated[
        User, Depends(require_permissions(Permission.VIEW_USERS))
    ],
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    role_name: RoleName | None = Query(default=None),
    is_active: bool | None = Query(default=None),
) -> PaginatedUsersResponse:
    users, total = await crud.get_users(
        db,
        skip=skip,
        limit=limit,
        search=search,
        role_name=role_name.value if role_name else None,
        is_active=is_active,
    )
    response.headers["X-Total-Count"] = str(total)
    return PaginatedUsersResponse(
        items=[UserOut.model_validate(u) for u in users],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user (Admin/Root)",
)
async def admin_create_user(
    db: DbSession,
    actor: Annotated[
        User, Depends(require_permissions(Permission.CREATE_USERS))
    ],
    payload: UserAdminCreateRequest,
) -> User:
    requested_roles: set[str] = {
        role.value for role in payload.initial_role_names
    }
    actor_is_root: bool = any(
        r.role_name == RoleName.ROOT.value
        for r in actor.roles
    )

    requests_system_roles: bool = bool(
        requested_roles & crud.SYSTEM_ROLE_NAMES
    )
    if requests_system_roles and not actor_is_root:
        raise InsufficientRolePermissionError(
            "Only ROOT can create users with ROOT or ADMIN roles."
        )

    return await crud.create_user(
        db,
        user_name=payload.user_name,
        user_email=str(payload.user_email),
        password=payload.password,
        initial_role_names=tuple(requested_roles),
    )


@router.get(
    "/{user_id}",
    response_model=UserDetailOut,
    summary="Get user by ID",
)
async def get_user_by_id(
    db: DbSession,
    current_user: CurrentUser,
    user_id: UUID,
    include_accesses: bool = Query(default=False),
) -> User:
    is_self: bool = current_user.user_id == user_id
    can_view_others: bool = (
        Permission.VIEW_USERS in get_user_permissions(current_user)
    )
    
    if not is_self and not can_view_others:
        raise InsufficientRolePermissionError(
            "You do not have permission to view other users."
        )

    return await crud.get_user_by_id(
        db,
        user_id,
        include_accesses=include_accesses,
    )


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    summary="Update user profile by ID",
)
async def update_user_by_id(
    db: DbSession,
    current_user: CurrentUser,
    user_id: UUID,
    payload: UserUpdateRequest,
) -> User:
    is_self: bool = current_user.user_id == user_id
    can_manage_others: bool = (
        Permission.MANAGE_USERS in get_user_permissions(current_user)
    )
    
    if not is_self and not can_manage_others:
        raise InsufficientRolePermissionError(
            "You do not have permission to update other users."
        )

    return await crud.update_user(
        db,
        actor_id=current_user.user_id,
        target_user_id=user_id,
        user_name=payload.user_name,
        user_email=str(payload.user_email) if payload.user_email else None,
    )


@router.delete(
    "/{user_id}",
    response_model=UserOut,
    summary="Deactivate user and revoke all roles & source accesses",
)
async def deactivate_user(
    db: DbSession,
    actor: Annotated[
        User, Depends(require_permissions(Permission.MANAGE_USERS))
    ],
    user_id: UUID,
) -> User:
    return await crud.deactivate_user(
        db,
        actor_id=actor.user_id,
        target_user_id=user_id,
    )


# System Roles Management


@router.post(
    "/{user_id}/roles",
    response_model=UserOut,
    summary="Assign a system role to a user",
)
async def assign_role(
    db: DbSession,
    actor: Annotated[
        User, Depends(require_permissions(Permission.MANAGE_ROLES))
    ],
    user_id: UUID,
    payload: UserRoleAssignRequest,
) -> User:
    return await crud.assign_role_to_user(
        db,
        actor_id=actor.user_id,
        target_user_id=user_id,
        role_id=payload.role_id,
    )


@router.delete(
    "/{user_id}/roles/{role_id}",
    response_model=UserOut,
    summary="Remove a system role from a user",
)
async def remove_role(
    db: DbSession,
    actor: Annotated[
        User, Depends(require_permissions(Permission.MANAGE_ROLES))
    ],
    user_id: UUID,
    role_id: int,
) -> User:
    return await crud.remove_role_from_user(
        db,
        actor_id=actor.user_id,
        target_user_id=user_id,
        role_id=role_id,
    )


# Energy Source Object-Level Access Control


@router.get(
    "/sources/{source_id}/accesses",
    response_model=list[UserSourceAccessOut],
    summary="List users with access to a specific energy source",
)
async def get_source_accesses(
    db: DbSession,
    current_user: CurrentUser,
    source_id: UUID,
) -> list:
    await crud.ensure_can_view_source_accesses(
        db,
        actor_id=current_user.user_id,
        source_id=source_id,
    )
    return await crud.get_source_access_list(db, source_id=source_id)


@router.post(
    "/sources/{source_id}/accesses",
    response_model=UserSourceAccessOut,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a user access to an energy source",
)
async def grant_source_access(
    db: DbSession,
    current_user: CurrentUser,
    source_id: UUID,
    payload: SourceAccessCreateRequest,
):
    return await crud.grant_user_source_access(
        db,
        actor_id=current_user.user_id,
        target_user_id=payload.user_id,
        source_id=source_id,
        role_id=payload.role_id,
    )


@router.patch(
    "/sources/{source_id}/accesses/{user_id}",
    response_model=UserSourceAccessOut,
    summary="Update a user's role on an energy source",
)
async def update_source_access(
    db: DbSession,
    current_user: CurrentUser,
    source_id: UUID,
    user_id: UUID,
    payload: SourceAccessUpdateRequest,
):
    return await crud.update_user_source_access(
        db,
        actor_id=current_user.user_id,
        target_user_id=user_id,
        source_id=source_id,
        new_role_id=payload.role_id,
    )


@router.delete(
    "/sources/{source_id}/accesses/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a user's access to an energy source",
)
async def revoke_source_access(
    db: DbSession,
    current_user: CurrentUser,
    source_id: UUID,
    user_id: UUID,
) -> Response:
    await crud.revoke_user_source_access(
        db,
        actor_id=current_user.user_id,
        target_user_id=user_id,
        source_id=source_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Maintenance Inspector Assignment


@router.post(
    "/maintenance-sessions/{session_id}/inspectors",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign a service engineer (inspector) to a maintenance session",
)
async def assign_inspector_to_session(
    db: DbSession,
    current_user: CurrentUser,
    session_id: UUID,
    payload: AssignInspectorRequest,
) -> Response:
    await crud.assign_inspector_to_maintenance_session(
        db,
        actor_id=current_user.user_id,
        session_id=session_id,
        inspector_id=payload.inspector_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
