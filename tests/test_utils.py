from app.utils import parse_display_name


def test_parse_display_name_two_parts():
    display, given, family = parse_display_name("Иван Петров")
    assert display == "Иван Петров"
    assert given == "Иван"
    assert family == "Петров"


def test_parse_display_name_single_word():
    display, given, family = parse_display_name("Мария")
    assert display == "Мария"
    assert given == "Мария"
    assert family is None


def test_parse_display_name_empty():
    display, given, family = parse_display_name(None)
    assert display == ""
    assert given is None
    assert family is None
