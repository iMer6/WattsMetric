from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import (
    table, Table, TableClause,
    and_,
    column,
    func,
    select, exists, delete,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import Base
from app.users.exceptions import *
from app.users.models import User, Role, UserRole, UserSourceAccess
from app.core.enums import RoleName

from jwt.exceptions import InvalidTokenError
from app.core.security import (
    hash_password,
    verify_password,
    verify_dummy_password,
    create_oauth2_access_token,
    decode_oauth2_access_token,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from app.core.utils import normalise_email, escape_like

SYSTEM_ROLE_NAMES: frozenset[str] = frozenset({
    RoleName.ROOT.value,
    RoleName.ADMIN.value,
})

SOURCE_STATUS_ACTIVE: str = "active"


def _get_sources_table() -> Table | TableClause:
    """
    Returns the metadata for the 'sources' table
    without circular imports between modules.
    """
    if "sources" in Base.metadata.tables:
        return Base.metadata.tables["sources"]
    
    return table(
        "sources",
        column("source_id", PG_UUID(as_uuid=True)),
        column("source_status"),
    )


def _get_maintenance_sessions_table() -> Table | TableClause:
    """
    Returns the metadata for the 'maintenance_sessions' table
    without circular imports between modules.
    """
    if "maintenance_sessions" in Base.metadata.tables:
        return Base.metadata.tables["maintenance_sessions"]
    
    return table(
        "maintenance_sessions",
        column("session_id", PG_UUID(as_uuid=True)),
        column("source_id", PG_UUID(as_uuid=True)),
    )


def _get_maintenance_inspectors_table() -> Table | TableClause:
    """
    Returns the metadata for the 'maintenance_inspectors' table
    without circular imports between modules.
    """
    if "maintenance_inspectors" in Base.metadata.tables:
        return Base.metadata.tables["maintenance_inspectors"]
    
    return table(
        "maintenance_inspectors",
        column("session_id", PG_UUID(as_uuid=True)),
        column("inspector_id", PG_UUID(as_uuid=True)),
    )


async def _get_maintenance_session_source_id(
    db: AsyncSession,
    session_id: UUID,
) -> UUID:
    """
    Checks for the existence of a service session
    and returns its source_id.
    """
    sessions: Table | TableClause = _get_maintenance_sessions_table()
    source_id_stmt = (
        select(sessions.columns.source_id)
        .where(session_id == sessions.columns.session_id)
    )
    source_id: UUID | None = await db.scalar(source_id_stmt)
    if source_id is None:
        raise MaintenanceSessionNotFoundError(
            f"Maintenance session {session_id} not found"
        )
    return source_id


# Integrity and access right checks


async def _ensure_source_exists(
    db: AsyncSession,
    source_id: UUID
) -> None:
    sources: Table | TableClause = _get_sources_table()
    
    source_exists_stmt = select(
        exists().where(source_id == sources.columns.source_id)
    )
    source_exists: bool | None = await db.scalar(source_exists_stmt)
    if not source_exists:
        raise SourceNotFoundError(f"Source {source_id} not found")


async def _user_has_system_role(
    db: AsyncSession,
    user_id: UUID,
) -> bool:
    user_has_system_role_stmt = select(
        exists().where(
            and_(
                UserRole.user_id == user_id,
                UserRole.role_id == Role.role_id,
                Role.role_name.in_(SYSTEM_ROLE_NAMES),
            )
        )
    )
    user_has_system_role: bool | None = (
        await db.scalar(user_has_system_role_stmt)
    )
    return bool(user_has_system_role)


def _ensure_valid_source_role(role: Role) -> None:
    """
    Prohibits the assignment of system roles (ROOT, ADMIN) to an object.
    Allows any non-system (default or owner-created) role.
    """
    is_role_in_system_role_names: bool = role.role_name in SYSTEM_ROLE_NAMES
    if is_role_in_system_role_names:
        raise InvalidSourceRoleError(
            f"System role '{role.role_name}' cannot be assigned to a source."
        )


async def _ensure_admin_permission(
    db: AsyncSession,
    actor_id: UUID
) -> None:
    is_user_admin: bool | None = await _user_has_system_role(
        db, actor_id
    )
    if not is_user_admin:
        raise InsufficientRolePermissionError(
            f"User {actor_id} does not have admin permission"
        )


async def _ensure_admin_or_root_permission(
    db: AsyncSession,
    actor_id: UUID
) -> bool:
    """
    Ensures the actor has either ROOT or ADMIN role (raises InsufficientPermissionError otherwise).
    Returns True if the actor has the ROOT role, False if only ADMIN.
    """
    stmt = (
        select(Role.role_name)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .where(
            and_(
                UserRole.user_id == actor_id,
                Role.role_name.in_(SYSTEM_ROLE_NAMES)
            )
        )
    )
    actor_system_roles: set[str] = set((await db.scalars(stmt)).all())
    if not actor_system_roles:
        raise InsufficientRolePermissionError(
            f"User {actor_id} does not have admin or root permission"
        )
    
    return RoleName.ROOT.value in actor_system_roles


async def _ensure_source_owner_or_admin(
    db: AsyncSession,
    *,
    actor_id: UUID,
    source_id: UUID
) -> None:
    if await _user_has_system_role(db, actor_id):
        return
    
    is_owner_stmt = select(
        exists().where(
            and_(
                UserSourceAccess.user_id == actor_id,
                UserSourceAccess.source_id == source_id,
                UserSourceAccess.role_id == Role.role_id,
                Role.role_name == RoleName.OWNER.value,
            )
        )
    )
    is_owner: bool | None = await db.scalar(is_owner_stmt)
    if not is_owner:
        raise NotSourceOwnerError(
            f"User {actor_id} does not have admin or owner permission "
            f"for source {source_id}"
        )


async def _ensure_not_last_root(
    db: AsyncSession,
    *,
    excluded_user_id: UUID
) -> None:
    """
    Blocks root rows and ensures that at least one root remains in the system.
    """
    root_role_id_stmt = (
        select(Role.role_id)
        .where(Role.role_name == RoleName.ROOT.value)
    )
    
    root_role_id: int | None = await db.scalar(root_role_id_stmt)
    if root_role_id is None:
        return
    
    root_user_ids_stmt = (
        select(UserRole.user_id)
        .join(User, User.user_id == UserRole.user_id)
        .where(
            and_(
                UserRole.role_id == root_role_id,
                User.is_active.is_(True),
            )
        )
        .with_for_update(of=UserRole)  # row-level lock to prevent race condition
    )
    
    root_user_ids: Sequence[UUID] = (
        (await db.scalars(root_user_ids_stmt)).all()
    )
    remaining_roots: list[UUID] = [
        uid for uid in root_user_ids if uid != excluded_user_id
    ]
    
    is_last_root: bool = len(remaining_roots) == 0
    if is_last_root:
        raise LastRootRoleRemovalError(
            f"User {excluded_user_id} is the last ROOT in the system"
        )


async def _ensure_not_last_admin(
    db: AsyncSession,
    *,
    excluded_user_id: UUID
) -> None:
    """
    Blocks administrator rows and ensures that at least one admin
    remains in the system.
    """
    admin_role_id_stmt = (
        select(Role.role_id)
        .where(Role.role_name == RoleName.ADMIN.value)
    )
    
    admin_role_id: int | None = await db.scalar(admin_role_id_stmt)
    if admin_role_id is None:
        return
    
    admin_user_ids_stmt = (
        select(UserRole.user_id)
        .join(User, User.user_id == UserRole.user_id)
        .where(
            and_(
                UserRole.role_id == admin_role_id,
                User.is_active.is_(True),
            )
        )
        .with_for_update(of=UserRole)  # row-level lock to prevent race condition
    )
    
    admin_user_ids: Sequence[UUID] = (
        (await db.scalars(admin_user_ids_stmt)).all()
    )
    remaining_admins: list[UUID] = [
        uid for uid in admin_user_ids if uid != excluded_user_id
    ]
    
    is_last_admin: bool = len(remaining_admins) == 0
    if is_last_admin:
        raise LastAdminRoleRemovalError(
            f"User {excluded_user_id} is the last admin in the system"
        )


async def _ensure_not_last_source_owner(
    db: AsyncSession,
    *,
    source_id: UUID,
    excluded_user_id: UUID
) -> None:
    """
    Blocks the deletion of source owners and ensures
    that at least one owner exists.
    """
    owner_role_id_stmt = (
        select(Role.role_id)
        .where(Role.role_name == RoleName.OWNER.value)
    )
    owner_role_id: int | None = await db.scalar(owner_role_id_stmt)
    if owner_role_id is None:
        return
    
    owner_ids_stmt = (
        select(UserSourceAccess.user_id)
        .where(
            and_(
                UserSourceAccess.source_id == source_id,
                UserSourceAccess.role_id == owner_role_id,
            )
        )
        .with_for_update()
    )
    owner_ids: Sequence[UUID] = (await db.scalars(owner_ids_stmt)).all()
    remaining_owners: list[UUID] = [
        uid for uid in owner_ids if uid != excluded_user_id
    ]
    
    is_last_owner: bool = len(remaining_owners) == 0
    if is_last_owner:
        raise LastSourceOwnerRemovalError(
            f"User {excluded_user_id} is the last source owner "
            f"for source {source_id}"
        )


async def _ensure_not_sole_owner_of_active_sources(
    db: AsyncSession,
    *,
    user_id: UUID
) -> None:
    """
    Verifies that each of the user's active sources will still have
    at least one other active OWNER.
    """
    sources: Table | TableClause = _get_sources_table()
    
    owner_role_id_stmt = (
        select(Role.role_id)
        .where(Role.role_name == RoleName.OWNER.value)
    )
    owner_role_id: int | None = await db.scalar(owner_role_id_stmt)
    if owner_role_id is None:
        return
    
    active_owned_stmt = (
        select(UserSourceAccess.source_id)
        .join(
            sources,
            UserSourceAccess.source_id == sources.columns.source_id
        )
        .where(
            and_(
                UserSourceAccess.user_id == user_id,
                UserSourceAccess.role_id == owner_role_id,
                sources.columns.source_status == SOURCE_STATUS_ACTIVE,
            )
        )
        .with_for_update(of=UserSourceAccess)
    )
    active_source_ids: Sequence[UUID] = (
        (await db.scalars(active_owned_stmt)).all()
    )
    if not active_source_ids:
        return
    
    for source_id in active_source_ids:
        other_owner_stmt = (
            select(UserSourceAccess.user_id)
            .join(User, User.user_id == UserSourceAccess.user_id)
            .where(
                and_(
                    UserSourceAccess.source_id == source_id,
                    UserSourceAccess.role_id == owner_role_id,
                    UserSourceAccess.user_id != user_id,
                    User.is_active.is_(True),
                )
            )
            .with_for_update(of=UserSourceAccess)
        )
        other_owner_ids: Sequence[UUID] = (await db.scalars(other_owner_stmt)).all()
        if not other_owner_ids:
            raise UserIsSoleOwnsActiveSourcesError(
                f"Source {source_id} does not have another owner"
            )


# Read (Users & Roles)


async def get_user_by_id(
    db: AsyncSession,
    user_id: UUID,
    *,
    include_accesses: bool = False
) -> User:
    stmt = (
        select(User)
        .where(User.user_id == user_id)
        .options(selectinload(User.roles))
    )
    if include_accesses:
        stmt = stmt.options(
            selectinload(User.source_accesses)
            .selectinload(UserSourceAccess.role)
        )
    
    user: User | None = await db.scalar(stmt)
    if user is None:
        raise UserNotFoundError(f"User {user_id} not found")
    return user


async def get_user_by_email(
    db: AsyncSession,
    email: str
) -> User | None:
    normalised_email: str = normalise_email(email)
    
    stmt = (
        select(User)
        .where(normalised_email == func.lower(User.user_email))
        .options(selectinload(User.roles))
    )
    user: User | None = await db.scalar(stmt)
    return user


async def get_users(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    role_name: str | None = None,
    is_active: bool | None = None
) -> tuple[list[User], int]:
    """
    Returns a paginated list of users (with optional loading of roles)
    and the total count.
    """
    filters: list = []
    
    if search and search.strip():
        escaped: str = f"%{escape_like(search.strip())}%"
        filters.append(
            User.user_name.ilike(escaped, escape="\\")
            | User.user_email.ilike(escaped, escape="\\")
        )
    
    if role_name:
        filters.append(
            exists().where(
                and_(
                    UserRole.user_id == User.user_id,
                    UserRole.role_id == Role.role_id,
                    Role.role_name == role_name,
                )
            )
        )
    
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    
    users_count_stmt = (
        select(func.count(User.user_id))
        .where(*filters)
    )
    total_users: int = int(await db.scalar(users_count_stmt) or 0)
    
    if total_users == 0:
        return [], 0
    
    users_stmt = (
        select(User)
        .where(*filters)
        .options(selectinload(User.roles))
        .order_by(
            User.created_at.desc(),
            User.user_id.asc()
        )
        .offset(skip)
        .limit(limit)
    )
    users: list[User] = list((await db.scalars(users_stmt)).all())
    return users, total_users


async def get_role_by_id(
    db: AsyncSession,
    role_id: int
) -> Role:
    role_stmt = (
        select(Role)
        .where(Role.role_id == role_id)
    )
    role: Role | None = await db.scalar(role_stmt)
    if role is None:
        raise RoleNotFoundError(f"Role {role_id} not found")
    
    return role


async def get_role_by_name(
    db: AsyncSession,
    role_name: str
) -> Role:
    role_stmt = (
        select(Role)
        .where(Role.role_name == role_name)
    )
    role: Role | None = await db.scalar(role_stmt)
    if role is None:
        raise RoleNotFoundError(f"Role {role_name} not found")
    return role


async def get_all_roles(db: AsyncSession) -> list[Role]:
    roles_stmt = (
        select(Role)
        .order_by(Role.role_id.asc())
    )
    roles: list[Role] = list((await db.scalars(roles_stmt)).all())
    return roles


# Authentication and validation of activities/roles


async def authenticate_user_oauth2(
    db: AsyncSession,
    *,
    email: str,
    password: str
) -> dict[str, str | int]:
    """
    OAuth 2.0 Password Flow: verifies credentials with protection against
    timing attacks, checks the 'is_active' status.
    Returns a JWT access token.
    """
    user: User | None = await get_user_by_email(db, email)
    if user is None:
        verify_dummy_password(password)
        raise InvalidCredentialsError(f"User with email {email} not found")
    
    is_password_verified: bool = verify_password(password, user.password_hash)
    if not is_password_verified:
        raise InvalidCredentialsError("Wrong password")
    
    if not user.is_active:
        raise UserDeactivatedError(f"User with email {email} is deactivated")
    
    role_names: list[str] = [role.role_name for role in user.roles]
    access_token: str = create_oauth2_access_token(
        client_id=user.user_id,
        roles=role_names,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


async def get_current_user_from_jwt(
    db: AsyncSession,
    *,
    token: str
) -> User:
    """
    Validates the OAuth 2.0 JWT Bearer token, loads the user from the DB.
    Checks whether the account has been deactivated since the token was issued.
    """
    try:
        payload: dict[str, Any] = decode_oauth2_access_token(token)
        user_id: UUID = UUID(str(payload["sub"]))
    except (InvalidTokenError, ValueError) as exc:
        raise InvalidCredentialsError(
            "Could not validate OAuth 2.0 JWT token",
        ) from exc
    
    user: User = await get_user_by_id(db, user_id)
    if not user.is_active:
        raise UserDeactivatedError(f"User {user_id} is deactivated")
    
    return user
        

async def validate_user_for_assignment(
    db: AsyncSession,
    *,
    user_id: UUID,
    required_inspector: bool = False
) -> User:
    """
    Checks the feasibility of assigning a user to an energy source
    or a maintenance session.
    """
    user: User = await get_user_by_id(db, user_id)
    if not user.is_active:
        raise CannotAssignDeactivatedUserError(
            f"User {user_id} is deactivated"
        )
    
    if required_inspector:
        has_inspector_role: bool = any(
            role.role_name == RoleName.ENGINEER.value for role in user.roles
        )
        if not has_inspector_role:
            raise UserNotInspectorError(
                f"User {user_id} does not have inspector role"
            )
    
    return user


# Create, update, change password and user deactivation


async def create_user(
    db: AsyncSession,
    *,
    user_name: str,
    user_email: str,
    password: str,
    initial_role_names: Sequence[str] = (RoleName.OWNER.value,)
) -> User:
    normalised_email: str = normalise_email(user_email)
    
    is_email_already_taken: bool = (
        await get_user_by_email(db, normalised_email) is not None
    )
    if is_email_already_taken:
        raise EmailAlreadyExistsError(
            f"Email {normalised_email} is already registered"
        )
    
    unique_role_names: list[str] = list(dict.fromkeys(initial_role_names))
    roles: list[Role] = []
    if unique_role_names:
        role_stmt = (
            select(Role)
            .where(Role.role_name.in_(unique_role_names))
        )
        roles = list(
            (await db.scalars(role_stmt)).all()
        )
        if len(roles) != len(unique_role_names):
            raise RoleNotFoundError()
    
    new_user: User = User(
        user_name=user_name.strip(),
        user_email=normalised_email,
        password_hash=hash_password(password),
    )
    
    try:
        db.add(new_user)
        await db.flush()
        
        if roles:
            db.add_all([
                UserRole(
                    user_id=new_user.user_id, role_id=role.role_id
                ) for role in roles
            ])
        
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        
        if "user_email" in str(exc.orig).lower():
            raise EmailAlreadyExistsError(
                f"Email {normalised_email} is already registered"
            ) from exc
        
        raise FailedToCreateUserError() from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToCreateUserError("Failed to create user") from exc
    
    new_user_by_id: User = await get_user_by_id(db, new_user.user_id)
    return new_user_by_id


async def update_user(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_user_id: UUID,
    user_name: str | None = None,
    user_email: str | None = None
) -> User:
    if actor_id != target_user_id:
        is_actor_root: bool = (
            await _ensure_admin_or_root_permission(db, actor_id)
        )
        target_user: User = await get_user_by_id(db, target_user_id)
        if (
            not is_actor_root
            and any(
                role.role_name == RoleName.ROOT.value
                for role in target_user.roles
            )
        ):
            raise InsufficientRolePermissionError(
                "Only ROOT can update a ROOT user"
            )
    
    user: User = await get_user_by_id(db, target_user_id)
    
    if user_email is not None:
        normalised_email: str = normalise_email(user_email)
        is_email_changed: bool = normalised_email != user.user_email.lower()
        
        if is_email_changed:
            existing: User | None = await get_user_by_email(db, normalised_email)
            is_email_already_taken: bool = (
                existing is not None and existing.user_id != target_user_id
            )
            
            if is_email_already_taken:
                raise EmailAlreadyExistsError(
                    f"Email {user_email} is already registered"
                )
            user.user_email = normalised_email
    
    if user_name is not None:
        user.user_name = user_name.strip()
    
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "user_email" in str(exc.orig).lower():
            raise EmailAlreadyExistsError(
                f"Email {user_email} is already registered"
            ) from exc
        raise FailedToUpdateUserError("Failed to update user") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError("Failed to update user") from exc
    
    updated_user: User = await get_user_by_id(db, target_user_id)
    return updated_user


async def change_user_password(
    db: AsyncSession,
    *,
    user_id: UUID,
    current_password: str,
    new_password: str
) -> User:
    user: User = await get_user_by_id(db, user_id)
    
    if not user.is_active:
        raise UserDeactivatedError(f"User {user_id} is deactivated")
    
    is_password_verified: bool = (
        verify_password(current_password, user.password_hash)
    )
    if not is_password_verified:
        raise InvalidPasswordError("Current password does not match")
    
    user.password_hash = hash_password(new_password)
    
    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError(
            "Failed to change user password"
        ) from exc
    
    return user


async def deactivate_user(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_user_id: UUID
) -> User:
    """
    Deactivates the user by revoking all active system roles, access right
    and set is_active=False.
    """
    is_self_deactivation: bool = actor_id == target_user_id
    if is_self_deactivation:
        raise SelfDeactivationNotAllowedError(
            "Self deactivation is not allowed"
        )
    
    user_stmt = (
        select(User)
        .where(User.user_id == target_user_id)
        .options(selectinload(User.roles))
        .with_for_update()
    )
    user: User | None = await db.scalar(user_stmt)
    if user is None:
        raise UserNotFoundError(f"User {target_user_id} is not found")
    
    if not user.is_active:
        raise UserAlreadyDeactivatedError(
            f"User {target_user_id} already deactivated"
        )
    
    target_role_names: set[str] = {role.role_name for role in user.roles}
    
    is_actor_root: bool = await _ensure_admin_or_root_permission(db, actor_id)
    if not is_actor_root and (target_role_names & SYSTEM_ROLE_NAMES):
        raise InsufficientRolePermissionError(
            "Only ROOT can deactivate ROOT or ADMIN users"
        )
    
    is_target_user_root: bool = RoleName.ROOT.value in target_role_names
    if is_target_user_root:
        await _ensure_not_last_root(db, excluded_user_id=target_user_id)
    
    is_target_user_admin: bool = RoleName.ADMIN.value in target_role_names
    if is_target_user_admin:
        await _ensure_not_last_admin(db, excluded_user_id=target_user_id)
    
    await _ensure_not_sole_owner_of_active_sources(
        db, user_id=target_user_id
    )
    
    try:
        user.is_active = False
        
        delete_user_roles_stmt = (
            delete(UserRole)
            .where(UserRole.user_id == target_user_id)
        )
        await db.execute(delete_user_roles_stmt)
        
        delete_user_source_accesses_stmt = (
            delete(UserSourceAccess)
            .where(UserSourceAccess.user_id == target_user_id)
        )
        await db.execute(delete_user_source_accesses_stmt)
        
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError(
            f"Failed to deactivate user {target_user_id}"
        ) from exc
    
    updated_user: User = await get_user_by_id(db, target_user_id)
    return updated_user


# Managing system roles


async def assign_role_to_user(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_user_id: UUID,
    role_id: int
) -> User:
    is_actor_root: bool = await _ensure_admin_or_root_permission(db, actor_id)
    await get_user_by_id(db, target_user_id)
    role: Role = await get_role_by_id(db, role_id)
    
    is_assign_system_role: bool = role.role_name in SYSTEM_ROLE_NAMES
    if is_assign_system_role and not is_actor_root:
        raise InsufficientRolePermissionError(
            f"Only ROOT can assign system role '{role.role_name}'"
        )
    
    already_assigned_stmt = (
        select(
            exists().where(
                and_(
                    UserRole.user_id == target_user_id,
                    UserRole.role_id == role_id,
                )
            )
        )
    )
    already_assigned: bool | None = await db.scalar(already_assigned_stmt)
    if already_assigned:
        raise UserAlreadyHasRoleError(
            f"Role {role_id} is already assigned to user {target_user_id}"
        )
    
    try:
        db.add(
            UserRole(user_id=target_user_id, role_id=role_id,)
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise UserAlreadyHasRoleError(
            f"Role {role_id} is already assigned to user {target_user_id}"
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError(
           f"Failed to assign role {role_id} to user {target_user_id}"
        ) from exc
    
    updated_user: User = await get_user_by_id(db, target_user_id)
    return updated_user


async def remove_role_from_user(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_user_id: UUID,
    role_id: int
) -> User:
    is_actor_root: bool = await _ensure_admin_or_root_permission(db, actor_id)
    await get_user_by_id(db, target_user_id)
    role: Role = await get_role_by_id(db, role_id)
    
    is_assign_system_role: bool = role.role_name in SYSTEM_ROLE_NAMES
    if is_assign_system_role and not is_actor_root:
        raise InsufficientRolePermissionError(
            f"Only ROOT can remove system role '{role.role_name}'"
        )
    
    has_role_stmt = (
        select(
            exists().where(
                and_(
                    UserRole.user_id == target_user_id,
                    UserRole.role_id == role_id,
                )
            )
        )
    )
    has_role: bool | None = await db.scalar(has_role_stmt)
    if not has_role:
        raise UserDoesNotHaveRoleError(
            f"User {target_user_id} does not have role {role_id}"
        )
    
    is_target_user_root: bool = role.role_name == RoleName.ROOT.value
    if is_target_user_root:
        await _ensure_not_last_root(db, excluded_user_id=target_user_id)
    
    is_target_user_admin: bool = role.role_name == RoleName.ADMIN.value
    if is_target_user_admin:
        await _ensure_not_last_admin(db, excluded_user_id=target_user_id)
    
    try:
        delete_user_roles_stmt = (
            delete(UserRole)
            .where(
                and_(
                    UserRole.user_id == target_user_id,
                    UserRole.role_id == role_id,
                )
            )
        )
        await db.execute(delete_user_roles_stmt)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError(
            f"Failed to remove role from user {target_user_id}"
        ) from exc
    
    updated_user: User = await get_user_by_id(db, target_user_id)
    return updated_user


async def get_source_access_list(
    db: AsyncSession,
    *,
    source_id: UUID
) -> list[UserSourceAccess]:
    await _ensure_source_exists(db, source_id)
    
    source_accesses_stmt = (
        select(UserSourceAccess)
        .where(UserSourceAccess.source_id == source_id)
        .options(
            selectinload(UserSourceAccess.user).selectinload(User.roles),
            selectinload(UserSourceAccess.role),
        )
        .order_by(UserSourceAccess.access_id.asc())
    )
    source_accesses: list[UserSourceAccess] = list(
        (await db.scalars(source_accesses_stmt)).all()
    )
    return source_accesses


async def get_user_source_access(
    db: AsyncSession,
    *,
    user_id: UUID,
    source_id: UUID
) -> UserSourceAccess:
    await _ensure_source_exists(db, source_id)
    await get_user_by_id(db, user_id)
    
    user_source_access_stmt = (
        select(UserSourceAccess)
        .where(
            and_(
                UserSourceAccess.user_id == user_id,
                UserSourceAccess.source_id == source_id,
            )
        )
        .options(
            selectinload(UserSourceAccess.user).selectinload(User.roles),
            selectinload(UserSourceAccess.role),
        )
    )
    user_source_access: UserSourceAccess | None = (
        await db.scalar(user_source_access_stmt)
    )
    if user_source_access is None:
        raise UserSourceAccessNotFoundError(
            f"User {user_id} source access {source_id} not found"
        )
    
    return user_source_access


async def grant_user_source_access(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_user_id: UUID,
    source_id: UUID,
    role_id: int
) -> UserSourceAccess:
    await _ensure_source_exists(db, source_id)
    await _ensure_source_owner_or_admin(
        db, actor_id=actor_id, source_id=source_id,
    )
    await validate_user_for_assignment(db, user_id=target_user_id)
    
    role: Role = await get_role_by_id(db, role_id)
    _ensure_valid_source_role(role)  # ROOT / ADMIN prohibition
    
    existing_access_stmt = (
        select(
            exists().where(
                and_(
                    UserSourceAccess.user_id == target_user_id,
                    UserSourceAccess.source_id == source_id,
                )
            )
        )
    )
    existing_access: bool | None = await db.scalar(existing_access_stmt)
    if existing_access:
        raise UserSourceAccessAlreadyExistsError(
            f"User {target_user_id} already have access to source {source_id}"
        )
    
    grant_access: UserSourceAccess = UserSourceAccess(
        user_id=target_user_id,
        source_id=source_id,
        role_id=role_id,
    )
    
    try:
        db.add(grant_access)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise UserSourceAccessAlreadyExistsError(
            f"User {target_user_id} already have access to source {source_id}"
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError(
            "Failed to grant access to source"
        ) from exc
    
    granted_user_source_access: UserSourceAccess = await get_user_source_access(
        db, user_id=target_user_id, source_id=source_id
    )
    return granted_user_source_access


async def update_user_source_access(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_user_id: UUID,
    source_id: UUID,
    new_role_id: int
) -> UserSourceAccess:
    await _ensure_source_exists(db, source_id)
    await _ensure_source_owner_or_admin(
        db, actor_id=actor_id, source_id=source_id,
    )
    await validate_user_for_assignment(db, user_id=target_user_id)  # is_active=True?
    
    new_role: Role = await get_role_by_id(db, new_role_id)
    _ensure_valid_source_role(new_role)
    
    user_source_access_stmt = (
        select(UserSourceAccess)
        .where(
            and_(
                UserSourceAccess.user_id == target_user_id,
                UserSourceAccess.source_id == source_id,
            )
        )
        .options(selectinload(UserSourceAccess.role))
        .with_for_update()
    )
    user_source_access: UserSourceAccess = (
        await db.scalar(user_source_access_stmt)
    )
    if user_source_access is None:
        raise UserSourceAccessNotFoundError(
            f"User {target_user_id} access to source {source_id} not found"
        )
    
    is_currently_owner: bool = (
        user_source_access.role.role_name == RoleName.OWNER.value
    )
    is_new_role_owner: bool = (
        new_role.role_name == RoleName.OWNER.value
    )
    if is_currently_owner and not is_new_role_owner:
        await _ensure_not_last_source_owner(
            db, source_id=source_id, excluded_user_id=target_user_id
        )
    
    user_source_access.role_id = new_role_id
    
    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError(
            "Failed to update user source access"
        ) from exc
    
    updated_user_source_access: UserSourceAccess = await get_user_source_access(
        db, user_id=target_user_id, source_id=source_id
    )
    return updated_user_source_access


async def revoke_user_source_access(
    db: AsyncSession,
    *,
    actor_id: UUID,
    target_user_id: UUID,
    source_id: UUID,
) -> None:
    await _ensure_source_exists(db, source_id)
    await _ensure_source_owner_or_admin(
        db, actor_id=actor_id, source_id=source_id,
    )
    await get_user_by_id(db, target_user_id)
    
    user_source_access_stmt = (
        select(UserSourceAccess)
        .where(
            and_(
                UserSourceAccess.user_id == target_user_id,
                UserSourceAccess.source_id == source_id,
            )
        )
        .options(selectinload(UserSourceAccess.role))
        .with_for_update()
    )
    user_source_access: UserSourceAccess = (
        await db.scalar(user_source_access_stmt)
    )
    if user_source_access is None:
        raise UserSourceAccessNotFoundError(
            f"User {target_user_id} have no access to source {source_id}"
        )
    
    is_owner: bool = (
        user_source_access.role.role_name == RoleName.OWNER.value
    )
    if is_owner:
        await _ensure_not_last_source_owner(
            db, source_id=source_id, excluded_user_id=target_user_id
        )
    
    try:
        await db.delete(user_source_access)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError(
            "Failed to revoke user source access"
        ) from exc


# Assigning an inspector to a maintenance session


async def assign_inspector_to_maintenance_session(
    db: AsyncSession,
    *,
    session_id: UUID,
    inspector_id: UUID,
) -> None:
    """
    Verifies the existence of a service session,
    the inspector's activity status and role,
    as well as their access to the energy source for that session.
    """
    source_id: UUID = await _get_maintenance_session_source_id(db, session_id)
    inspector: User = await validate_user_for_assignment(
        db, user_id=inspector_id, required_inspector=True
    )
    
    is_system_staff: bool = any(
        role.role_name in SYSTEM_ROLE_NAMES
        for role in inspector.roles
    )
    if not is_system_staff:
        has_source_access_stmt = (
            select(
                exists().where(
                    and_(
                        UserSourceAccess.user_id == inspector_id,
                        UserSourceAccess.source_id == source_id,
                    )
                )
            )
        )
        has_source_access: bool | None = await db.scalar(has_source_access_stmt)
        if not has_source_access:
            raise InspectorHasNoSourceAccessError(
                f"Inspector {inspector_id} does not have access "
                f"to source {source_id}"
            )
    
    inspectors_table: Table | TableClause = (
        _get_maintenance_inspectors_table()
    )
    insert_inspector_stmt = (
        pg_insert(inspectors_table)
        .values(session_id=session_id, inspector_id=inspector_id)
        .on_conflict_do_nothing(
            index_elements=["session_id", "inspector_id"]
        )
    )
    
    try:
        await db.execute(insert_inspector_stmt)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise FailedToUpdateUserError(
            "Failed to assign inspector to maintenance session"
        ) from exc
