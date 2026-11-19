from app.core.exceptions import (
    NotFoundError,
    ConflictError,
    InsufficientPermissionError,
    BusinessRuleError,
    InternalServerError,
)


# 404 Not found


class UserNotFoundError(NotFoundError):
    default_message = "The user does not exist."


class RoleNotFoundError(NotFoundError):
    default_message = "The specified role does not exist."


class SourceNotFoundError(NotFoundError):
    default_message = "The specified energy source does not exist."


class UserSourceAccessNotFoundError(NotFoundError):
    default_message = "User access record for this source does not exist."


class MaintenanceSessionNotFoundError(NotFoundError):
    default_message = "Maintenance session record for this session does not exist."


# 403 Forbidden


class InvalidCredentialsError(InsufficientPermissionError):
    default_message = "Invalid email or password."


class InvalidSourceRoleError(InsufficientPermissionError):
    default_message = "Invalid source role."


class NotSourceOwnerError(InsufficientPermissionError):
    default_message = "Only the source owner can manage user access for this source."


class InsufficientRolePermissionError(InsufficientPermissionError):
    default_message = "Insufficient permissions to manage this user."


class UserNotInspectorError(InsufficientPermissionError):
    default_message = "The specified user does not have the inspector role."


class UserDeactivatedError(InsufficientPermissionError):
    default_message = "This user account has been deactivated."


class InspectorHasNoSourceAccessError(InsufficientPermissionError):
    default_message = "Inspector has no source access record."


# 409 Conflict


class EmailAlreadyExistsError(ConflictError):
    default_message = "The email is already taken by another user."


class UserAlreadyHasRoleError(ConflictError):
    default_message = "The user already has this role assigned."


class UserAlreadyDeactivatedError(ConflictError):
    default_message = "The user is already deactivated."


class UserSourceAccessAlreadyExistsError(ConflictError):
    default_message = "The user already has access to this source."


class UserIsSoleOwnsActiveSourcesError(ConflictError):
    default_message = (
        "User cannot be deactivated they are the sole owner of active energy sources. "
        "Assign another owner or deactivate the sources first."
    )


# 422 Unprocessable Entity


class UserDoesNotHaveRoleError(BusinessRuleError):
    default_message = "The user does not have the specified role."


class LastRootRoleRemovalError(BusinessRuleError):
    default_message = "Cannot remove the ROOT role from the last system ROOT"


class LastAdminRoleRemovalError(BusinessRuleError):
    default_message = "Cannot remove the admin role from the last system administrator."


class LastSourceOwnerRemovalError(BusinessRuleError):
    default_message = "Cannot remove the last owner of an energy source."


class InvalidPasswordError(BusinessRuleError):
    default_message = "The provided current password is incorrect."


class CannotAssignDeactivatedUserError(BusinessRuleError):
    default_message = (
        "The user is already deactivated"
        "and cannot be assigned to energy source or maintenance session."
    )


class SelfDeactivationNotAllowedError(BusinessRuleError):
    default_message = "Self-deactivation is not allowed for this user."


# 500 Internal Server Error


class FailedToCreateUserError(InternalServerError):
    default_message = "Failed to create the user in the database."


class FailedToUpdateUserError(InternalServerError):
    default_message = "Failed to update the user in the database."
