import secrets

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import settings

_BCRYPT_ROUNDS: int = 12
_MAX_BCRYPT_BYTES: int = 72


def _is_password_length_valid(password_bytes: bytes) -> bool:
    return len(password_bytes) <= _MAX_BCRYPT_BYTES


def hash_password(plain_password: str) -> str:
    password_bytes: bytes = plain_password.encode("utf-8")
    
    if not _is_password_length_valid(password_bytes):
        raise ValueError("Password cannot exceed 72 bytes")
    
    salt: bytes = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes: bytes = plain_password.encode("utf-8")
    
    if not _is_password_length_valid(password_bytes):
        return False
    
    try:
        return bcrypt.checkpw(
            password_bytes,
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        return False
    

_DUMMY_PASSWORD_HASH: str = hash_password(secrets.token_urlsafe(32))


def verify_dummy_password(plain_password: str) -> bool:
    """
    Dummy hash check to protect against timing attacks.
    """
    return verify_password(plain_password, _DUMMY_PASSWORD_HASH)


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
        options={
            "require": ["exp", "iat", "sub"],
        },
    )
    
    is_valid_payload: bool = (
        payload.get("sub") is not None
        and payload.get("token_type") == "bearer"
    )
    if not is_valid_payload:
        raise InvalidTokenError("Invalid token payload")
    return payload
