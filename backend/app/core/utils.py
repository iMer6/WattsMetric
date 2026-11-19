def normalise_email(email: str) -> str:
    return email.strip().lower()


def escape_like(value: str) -> str:
    # noinspection SpellCheckingInspection
    """
    Escapes special characters for LIKE/ILIKE to protect against wildcard injection.
    """
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
