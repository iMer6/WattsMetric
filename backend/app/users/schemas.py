from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    AfterValidator,
)

from app.core.enums import RoleName
from app.core.utils import validate_strong_password


StrongPassword: type[str] = Annotated[
    str,
    Field(..., min_length=8, max_length=72),
    AfterValidator(validate_strong_password),
]


# Auth & Tokens


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# Roles


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role_id: int
    role_name: str
    description: str | None = None


class UserRoleAssignRequest(BaseModel):
    role_id: int = Field(..., gt=0)


# Users


class UserBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    user_name: str = Field(..., min_length=2, max_length=100)
    user_email: EmailStr = Field(..., max_length=255)


class UserRegisterRequest(UserBase):
    """
    Public registration of a station owner.
    The OWNER role is assigned automatically.
    """
    password: str = StrongPassword


class UserAdminCreateRequest(UserBase):
    """
    Creation of a user by an admin/ROOT with the selection of initial roles.
    """
    password: str = StrongPassword
    initial_role_names: list[RoleName] = Field(
        default=[RoleName.OWNER],
        min_length=1,
    )


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    user_name: str | None = Field(
        default=None, min_length=2, max_length=100
    )
    user_email: EmailStr | None = Field(default=None, max_length=255)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = StrongPassword


class UserShortOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    user_name: str
    user_email: EmailStr
    is_active: bool


class UserOut(UserShortOut):
    created_at: datetime
    roles: list[RoleOut] = []


class UserSourceAccessBriefOut(BaseModel):
    """
    User access to generation objects (for a user profile).
    """
    model_config = ConfigDict(from_attributes=True)

    access_id: int
    source_id: UUID
    role: RoleOut


class UserDetailOut(UserOut):
    source_accesses: list[UserSourceAccessBriefOut] = []


class PaginatedUsersResponse(BaseModel):
    items: list[UserOut]
    total: int
    skip: int
    limit: int


# Source Access & Maintenance Assignments


class SourceAccessCreateRequest(BaseModel):
    user_id: UUID
    role_id: int = Field(..., gt=0)


class SourceAccessUpdateRequest(BaseModel):
    role_id: int = Field(..., gt=0)


class UserSourceAccessOut(BaseModel):
    """
    Full access record for a generation facility
    (for the list of the facility's participants).
    """
    model_config = ConfigDict(from_attributes=True)

    access_id: int
    user_id: UUID
    source_id: UUID
    role: RoleOut
    user: UserShortOut


class AssignInspectorRequest(BaseModel):
    inspector_id: UUID
