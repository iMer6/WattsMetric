from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    InternalServerError,
    NotFoundError,
)


# 404 Not Found


class IoTDeviceNotFoundError(NotFoundError):
    default_message = "The specified IoT device does not exist."


# 409 Conflict


class IoTDeviceMacAlreadyExistsError(ConflictError):
    default_message = "An IoT device with this MAC address is already registered."


# 422 Unprocessable Entity


class InvalidMacAddressError(BusinessRuleError):
    default_message = "Invalid ESP32 MAC address format."


class IoTDeviceInactiveError(BusinessRuleError):
    default_message = "The specified IoT device is deactivated."


# 500 Internal Server Error


class FailedToCreateIoTDeviceError(InternalServerError):
    default_message = "Failed to register the IoT device in the database."


class FailedToUpdateIoTDeviceError(InternalServerError):
    default_message = "Failed to update the IoT device in the database."


class FailedToDeleteIoTDeviceError(InternalServerError):
    default_message = "Failed to delete the IoT device from the database."
