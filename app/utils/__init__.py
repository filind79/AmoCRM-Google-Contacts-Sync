import re
from typing import List, Optional, Tuple

from .phone import normalize_phone

__all__ = [
    "normalize_phone",
    "unique",
    "normalize_email",
    "EMAIL_REGEX",
    "is_valid_email",
    "parse_display_name",
]


def unique(sequence: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in sequence:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_email(email: str) -> str:
    return email.strip().lower()


EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


def parse_display_name(name: object) -> Tuple[str, Optional[str], Optional[str]]:
    """Split a raw display name into given and family parts."""

    if name is None:
        return "", None, None
    text = str(name)
    display = text.strip()
    if not display:
        return "", None, None
    parts = display.split(maxsplit=1)
    given = parts[0] if parts else None
    family = parts[1].strip() if len(parts) > 1 else None
    if family == "":
        family = None
    return display, given, family
