def normalize_identity_value(kind: str, value: str) -> str:
    """GitHub logins and email addresses are both case-insensitive; collapse them before uniqueness."""
    return value.strip().casefold()
