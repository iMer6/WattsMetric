import os
import secrets

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

_pwd_context: CryptContext = CryptContext(
    schemes=["bcrypt"], deprecated="auto"
)
_DUMMY_PASSWORD_HASH: str = _pwd_context.hash(secrets.token_urlsafe(32))

JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 60


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
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    expire: datetime | timedelta = now + delta
    
    payload: dict[str, Any] = {
        "sub": str(client_id),
        "roles": roles,
        "token_type": "bearer",
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def decode_oauth2_access_token(token: str) -> dict[str, Any]:
    """
    Validates and decodes a JWT token.
    Throws an InvalidTokenError if an error occurs.
    """
    payload = jwt.decode(
        token,
        JWT_SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
    )
    
    is_valid_payload: bool = (
        payload.get("sub") is not None
        and payload.get("token_type") == "bearer"
    )
    if not is_valid_payload:
        raise InvalidTokenError("Invalid token payload")
    return payload
