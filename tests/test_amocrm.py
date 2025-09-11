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
