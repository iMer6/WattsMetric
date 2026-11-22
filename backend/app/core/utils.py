import re

from datetime import datetime, timezone
from decimal import Decimal


PASSWORD_PATTERN: re.Pattern[str] = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).+$"
)


def validate_strong_password(value: str) -> str:
    is_valid_length: bool = len(value.encode("utf-8")) <= 72
    if not is_valid_length:
        raise ValueError("Password must not exceed 72 bytes (bcrypt limit).")
    if not PASSWORD_PATTERN.match(value):
        raise ValueError(
            "Password must contain at least one uppercase letter, "
            "one lowercase letter, and one digit."
        )
    return value


MAC_ADDRESS_PATTERN: re.Pattern[str] = re.compile(
    r"^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$|^[0-9a-fA-F]{12}$"
)


def normalise_mac_address(value: str) -> str:
    """
    Validate and canonicalise a 6-byte EUI-48 MAC address
    to lower colon format.
    """
    cleaned: str = value.strip()
    is_valid_mac_address: re.Match[str] | None = MAC_ADDRESS_PATTERN.match(cleaned)
    
    if not is_valid_mac_address:
        raise ValueError(
            "Invalid MAC address format. Expected 6-byte EUI-48 address "
            "(e.g., 'AA:BB:CC:DD:EE:FF')."
        )
    
    hex_only: str = (
        cleaned
        .replace(":", "")
        .replace("-", "")
        .lower()
    )
    
    octets: list[str] = [hex_only[i: i + 2] for i in range(0, 12, 2)]
    return ":".join(octets)


def normalise_email(email: str) -> str:
    """
    Trim whitespaces and lowercase an email address
    for consistent storage/lookup.
    """
    return email.strip().lower()


def escape_like(value: str) -> str:
    # noinspection SpellCheckingInspection
    """
    Escapes special characters for SQL LIKE/ILIKE
    to protect against wildcard injection.
    """
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


MIN_LATITUDE: Decimal = Decimal("-90")
MAX_LATITUDE: Decimal = Decimal("90")


def validate_lat(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None

    is_out_of_bounds: bool = value < MIN_LATITUDE or value > MAX_LATITUDE
    if is_out_of_bounds:
        raise ValueError("Latitude must be between -90 and 90 degrees.")

    return value


MIN_LONGITUDE: Decimal = Decimal("-180")
MAX_LONGITUDE: Decimal = Decimal("180")


def validate_lon(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None

    is_out_of_bounds: bool = value < MIN_LONGITUDE or value > MAX_LONGITUDE
    if is_out_of_bounds:
        raise ValueError("Longitude must be between -180 and 180 degrees.")

    return value


def ensure_utc_datetime(value: datetime) -> datetime:
    is_naive: bool = (
        value.tzinfo is None
        or value.tzinfo.utcoffset(value) is None
    )
    if is_naive:
        raise ValueError(
            "Timestamp must be timezone-aware (ISO 8601 with timezone offset or Z)"
        )
    return value.astimezone(timezone.utc)
