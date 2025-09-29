from __future__ import annotations

import re
from typing import Optional


def normalize_phone(e164: str, *, min_digits: int = 10) -> Optional[str]:
    """Normalise a raw phone number into E.164-like format."""

    if not e164:
        return None
    digits = re.sub(r"\D", "", e164)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) < min_digits:
        return None
    return "+" + digits
