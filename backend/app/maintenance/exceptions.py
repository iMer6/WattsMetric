from app.core.exceptions import (
    BusinessRuleError,
    InsufficientPermissionError,
    InternalServerError,
    NotFoundError,
)


# 403 Forbidden


class InvalidInspectorAssignmentError(InsufficientPermissionError):
    default_message = (
        "One or more inspectors are inactive, lack the ENGINEER role, "
        "or do not have access to this energy source."
    )


# 404 Not found


class MaintenanceSessionNotFoundError(NotFoundError):
    default_message = "Maintenance session record does not exist."


class MaintenancePhotoNotFoundError(NotFoundError):
    default_message = "Maintenance photo record does not exist."


# 422 Unprocessable Entity


class InvalidMaintenanceTimestampError(BusinessRuleError):
    default_message = "Maintenance session and photo timestamps must be timezone-aware."


# 500 Internal Server Error


class FailedToSyncMaintenanceError(InternalServerError):
    default_message = "Failed to synchronize maintenance session or photos."
