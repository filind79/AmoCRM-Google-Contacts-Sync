import re
from typing import List, Optional, Tuple


def normalize_phone(phone: str) -> Optional[str]:
    """Return an E.164 compliant phone representation if possible.

    The function strips all non-digit characters, normalises Russian leading
    ``8`` to ``+7`` and handles the European ``00`` international prefix.  If
    less than ten digits remain after normalisation the value is treated as
    noise and ``None`` is returned.
    """

    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) < 10:
        return None
    return "+" + digits


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
    """Split a raw display name into given and family parts.

    The function returns a tuple of ``(display_name, given_name, family_name)``.
    ``display_name`` preserves the original string value converted to ``str``
    with surrounding whitespace stripped.  ``given_name`` is the first word in
    the name (using whitespace as a separator) and ``family_name`` contains the
    remainder if present.  When the input is empty or contains only whitespace
    the function returns empty display name and ``None`` components.
    """

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
