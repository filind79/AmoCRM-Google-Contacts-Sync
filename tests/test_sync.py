from app.sync import dry_run_compare


def test_dry_run_compare():
    amo_contacts = [
        {"id": 1, "name": "Name1", "emails": ["A@ex.com"], "phones": ["+111"]},
        {"id": 2, "name": "Name2", "emails": [], "phones": ["+222"]},
        {"id": 3, "name": "NoKey", "emails": [], "phones": []},
    ]
    google_contacts = [
        {"resourceName": "people/1", "name": "Other", "emails": ["a@ex.com"], "phones": []},
        {"resourceName": "people/2", "name": "G2", "emails": [], "phones": ["+333"]},
        {"resourceName": "people/3", "name": "Dup", "emails": ["a@ex.com"], "phones": []},
        {"resourceName": "people/4", "name": "NoKey", "emails": [], "phones": []},
    ]
    result = dry_run_compare(amo_contacts, google_contacts, "both")
    assert result["amo"] == {"fetched": 3, "with_keys": 1, "skipped_no_keys": 2}
    assert result["google"] == {"fetched": 4, "with_keys": 2, "skipped_no_keys": 2}
    assert result["match"] == {"pairs": 1, "amo_only": 0, "google_only": 1}
    assert result["actions"]["amo_to_google"]["create"] == 0
    assert result["actions"]["amo_to_google"]["update"] == 1
    assert result["actions"]["google_to_amo"]["create"] == 1
    assert result["actions"]["google_to_amo"]["update"] == 1
