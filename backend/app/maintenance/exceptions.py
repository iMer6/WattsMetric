from app.core.exceptions import (
    BusinessRuleError,
    InsufficientPermissionError,
    InternalServerError,
    NotFoundError, ConflictError,
)


# 403 Forbidden


class InvalidInspectorAssignmentError(InsufficientPermissionError):
    default_message = (
        "One or more inspectors are inactive, lack the ENGINEER role, "
        "or do not have access to this energy source."
    )


# 404 Not found


class MaintenanceSessionNotFoundError(NotFoundError):
    default_message = (
        "The specified maintenance session does not exist "
        "or does not belong to this source."
    )


class MaintenancePhotoNotFoundError(NotFoundError):
    default_message = "Maintenance photo record does not exist."


class InspectorNotFoundError(NotFoundError):
    default_message = (
        "One or more specified service inspectors do not exist "
        "or are inactive."
    )


# 409 Conflict


class MaintenanceSessionConflictError(ConflictError):
    default_message = (
        "Maintenance session ID belongs to a different source "
        "and cannot be overwritten."
    )


# 413 Payload too large


class PhotoFileTooLargeError(BusinessRuleError):
    status_code: int = 413
    default_message = (
        "Uploaded photo file exceeds the max allowed size."
    )


# 422 Unprocessable Entity


class InvalidMaintenanceTimestampError(BusinessRuleError):
    default_message = (
        "Maintenance session and photo timestamps must be timezone-aware."
    )


class InvalidMaintenanceUploadError(BusinessRuleError):
    default_message = (
        "Invalid file upload. "
        "Only non-empty JPEG, PNG, and WebP images are permitted."
    )


# 500 Internal Server Error


class FailedToSyncMaintenanceError(InternalServerError):
    default_message = "Failed to synchronize maintenance session or photos."
