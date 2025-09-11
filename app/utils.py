import re
from typing import List


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


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
