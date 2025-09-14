from app.utils import normalize_phone, normalize_email, unique, is_valid_email


def test_normalize_phone():
    assert normalize_phone("8 (999) 111-22-33") == "+79991112233"


def test_normalize_email_and_validation():
    email = "  USER@Example.Com "
    normalized = normalize_email(email)
    assert normalized == "user@example.com"
    assert is_valid_email(normalized)


def test_unique():
    data = ["a", "b", "a"]
    assert unique(data) == ["a", "b"]
