from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    InsufficientPermissionError,
    InternalServerError,
    NotFoundError,
)


# 403 Forbidden


class SourceAccessDeniedError(InsufficientPermissionError):
    default_message = "You do not have permission to access or modify this energy source."


class SourceDeactivatedError(InsufficientPermissionError):
    default_message = "This energy source has been deactivated."


# 404 Not Found


class SourceNotFoundError(NotFoundError):
    default_message = "The specified energy source does not exist."


# 409 Conflict


class SourceQrCodeAlreadyExistsError(ConflictError):
    default_message = "An energy source with this QR code already exists."


class SourceAlreadyDeactivatedError(ConflictError):
    default_message = "The energy source is already deactivated."


# 422 Unprocessable Entity


class InvalidSourceCoordinatesError(BusinessRuleError):
    default_message = "Invalid geographic coordinates for energy source."


class CannotModifyDeactivatedSourceError(BusinessRuleError):
    default_message = "Deactivated energy source cannot be modified."


# 500 Internal Server Error


class FailedToCreateSourceError(InternalServerError):
    default_message = "Failed to create the energy source in the database."


class FailedToUpdateSourceError(InternalServerError):
    default_message = "Failed to update the energy source in the database."


class FailedToDeleteSourceError(InternalServerError):
    default_message = "Failed to delete the energy source from the database."


class FailedToDeactivateSourceError(InternalServerError):
    default_message = "Failed to deactivate the energy source in the database."
