from app.amocrm import extract_name_and_fields


def test_extract_name_and_fields():
    contact = {
        "name": "John Doe",
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": "+123"}]},
            {"field_code": "EMAIL", "values": [{"value": "test@example.com"}]},
        ],
    }
    result = extract_name_and_fields(contact)
    assert result["name"] == "John Doe"
    assert result["phones"] == ["+123"]
    assert result["emails"] == ["test@example.com"]


def test_extract_handles_none_custom_fields():
    contact = {"name": "John", "custom_fields_values": None}
    result = extract_name_and_fields(contact)
    assert result["name"] == "John"
    assert result["phones"] == []
    assert result["emails"] == []


def test_extract_handles_missing_and_empty_structures():
    contacts = [
        {"first_name": "Jane", "last_name": "Doe"},
        {"name": "A", "custom_fields_values": []},
        {"name": "B", "custom_fields_values": [{}]},
        {"name": "C", "custom_fields_values": [{"field_code": "PHONE"}]},
        {"name": "D", "custom_fields_values": [{"values": []}]},
        {"name": "E", "custom_fields_values": [{"field_code": "EMAIL", "values": [{}]}]},
    ]
    for c in contacts:
        result = extract_name_and_fields(c)
        assert result["phones"] == []
        assert result["emails"] == []
    assert extract_name_and_fields(contacts[0])["name"] == "Jane Doe"


def test_extract_normalizes_values() -> None:
    contact = {
        "name": "John",
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": "8 (999) 111-22-33"}]},
            {"field_code": "EMAIL", "values": [{"value": " User@MAIL.com "}]},
        ],
    }
    result = extract_name_and_fields(contact)
    assert result["phones"] == ["+79991112233"]
    assert result["emails"] == ["user@mail.com"]
