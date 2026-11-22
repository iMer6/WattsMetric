import logging
from logging import Logger

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger: Logger = logging.getLogger("watts_metric.exceptions")


class BaseError(Exception):
    """Base exception for all business logic."""
    status_code: int = status.HTTP_400_BAD_REQUEST
    default_message: str = "Application error"
    
    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class NotFoundError(BaseError):
    status_code: int = status.HTTP_404_NOT_FOUND
    default_message: str = "Resource not found"


class ConflictError(BaseError):
    status_code: int = status.HTTP_409_CONFLICT
    default_message: str = "Resource conflict"


class InsufficientPermissionError(BaseError):
    status_code: int = status.HTTP_403_FORBIDDEN
    default_message: str = "Permission denied"


class BusinessRuleError(BaseError):
    status_code: int = status.HTTP_422_UNPROCESSABLE_CONTENT
    default_message: str = "Business rule violation"


class InternalServerError(BaseError):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message: str = "Internal operation failed"


def register_exception_handlers(app: FastAPI) -> None:
    """Registers a single handler for all domain errors."""
    @app.exception_handler(BaseError)
    async def app_exception_handler(
        _request: Request,
        exc: BaseError
    ) -> JSONResponse:
        is_internal_server_error: bool = (
            exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        if is_internal_server_error:
            logger.exception(
                "Unhandled internal domain error: %s", exc
            )
            detail: str = "Internal server error"
        else:
            detail: str = exc.message
        
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": detail,
            },
        )
