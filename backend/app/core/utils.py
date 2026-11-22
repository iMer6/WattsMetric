import re

from datetime import datetime, timezone


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
