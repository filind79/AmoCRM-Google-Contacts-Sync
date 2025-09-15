from app.sync import build_google_lookup, is_existing_in_google


def test_is_existing_in_google_match():
    google = [{"emails": ["a@example.com"], "phones": ["+7 (999) 111-22-33"]}]
    lookup = build_google_lookup(google)
    amo_email = {"emails": ["A@Example.com"], "phones": []}
    assert is_existing_in_google(amo_email, lookup)
    amo_phone = {"emails": [], "phones": ["8(999)111-22-33"]}
    assert is_existing_in_google(amo_phone, lookup)


def test_is_existing_in_google_no_match():
    google = [{"emails": ["a@example.com"], "phones": ["+7 (999) 111-22-33"]}]
    lookup = build_google_lookup(google)
    amo = {"emails": ["b@example.com"], "phones": ["123"]}
    assert not is_existing_in_google(amo, lookup)

