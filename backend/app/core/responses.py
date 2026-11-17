from fastapi import status

COMMON_RESPONSES: dict[int, dict[str, str]] = {
    status.HTTP_401_UNAUTHORIZED: {
        "description": "Unauthorized – missing, invalid or expired auth token",
    },
    status.HTTP_403_FORBIDDEN: {
        "description": "Forbidden – insufficient permissions to access this resource"
    },
    status.HTTP_404_NOT_FOUND: {
        "description": "Not found – the requested resource does not exist",
    }
}