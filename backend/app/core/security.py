import secrets

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context: CryptContext = CryptContext(
    schemes=["bcrypt"], deprecated="auto"
)
_DUMMY_PASSWORD_HASH: str = _pwd_context.hash(secrets.token_urlsafe(32))


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def verify_dummy_password(plain_password: str) -> bool:
    """
    Dummy hash check to protect against timing attacks.
    """
    return _pwd_context.verify(plain_password, _DUMMY_PASSWORD_HASH)


def create_oauth2_access_token(
    *,
    client_id: UUID,
    roles: list[str],
    expires_delta: timedelta | None = None,
) -> str:
    """
    Generate a JWT access token for OAuth 2.0 Bearer authorisation.
    """
    now: datetime = datetime.now(timezone.utc)
    delta: timedelta = (
        expires_delta
        or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    expire: datetime = now + delta
    
    payload: dict[str, Any] = {
        "sub": str(client_id),
        "roles": roles,
        "token_type": "bearer",
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_oauth2_access_token(token: str) -> dict[str, Any]:
    """
    Validates and decodes a JWT token.
    Throws an InvalidTokenError if an error occurs.
    """
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithms=[settings.JWT_ALGORITHM],
    )
    
    is_valid_payload: bool = (
        payload.get("sub") is not None
        and payload.get("token_type") == "bearer"
    )
    if not is_valid_payload:
        raise InvalidTokenError("Invalid token payload")
    return payload
