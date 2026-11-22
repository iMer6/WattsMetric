from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    InternalServerError,
    NotFoundError,
)


# 404 Not found


class TestProfileNotFoundError(NotFoundError):
    default_message = "The specified test profile does not exist."


# 409 Conflict


class TestProfileAlreadyExistsError(ConflictError):
    default_message = "A test profile with this name already exists."


# 422 Unprocessable Entity


class InvalidTimeRangeError(BusinessRuleError):
    default_message = "'date_from' must be earlier than or equal to 'date_to'."


class InvalidTelemetryPayloadError(BusinessRuleError):
    default_message = "Telemetry measurement timestamps must be timezone-aware."


class TelemetryBatchLimitExceededError(BusinessRuleError):
    default_message = (
        "Telemetry batch exceeds the max allowed number of records per request."
    )


class TimeRangeTooLargeError(BusinessRuleError):
    default_message = (
        "Requested time interval exceeds the max allowed query window."
    )


# 500 Internal Server Error


class FailedToIngestTelemetryError(InternalServerError):
    default_message = "Failed to store telemetry measurements in the database."


class FailedToUpsertAggregateError(InternalServerError):
    default_message = "Failed to save time aggregate bucket in the database."


class FailedToManageTestProfileError(InternalServerError):
    default_message = "Failed to process test profile operation in the database."
