import pytest

from app.utils import is_valid_email, normalize_email, normalize_phone, unique


@pytest.mark.parametrize(
    "raw, normalized",
    [
        ("+375 (29) 123-45-67", "+375291234567"),
        ("8 (999) 111-22-33", "+79991112233"),
        ("0049 89 1234567", "+49891234567"),
        ("+44 20 7946-0958", "+442079460958"),
    ],
)
def test_normalize_phone_formats(raw: str, normalized: str) -> None:
    assert normalize_phone(raw) == normalized


@pytest.mark.parametrize(
    "raw, normalized",
    [
        ("User@MAIL.com ", "user@mail.com"),
        (" test.user+tag@GMail.COM", "test.user+tag@gmail.com"),
        ("\tPerson@Example.ru\n", "person@example.ru"),
        ("CUSTOMER@tut.by", "customer@tut.by"),
    ],
)
def test_normalize_email_formats(raw: str, normalized: str) -> None:
    assert normalize_email(raw) == normalized
    assert is_valid_email(normalized)


def test_unique() -> None:
    data = ["a", "b", "a"]
    assert unique(data) == ["a", "b"]
