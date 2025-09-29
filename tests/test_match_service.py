import pytest

from app.services.match import MatchKeys, search_google_candidates


@pytest.mark.asyncio
async def test_search_candidates_queries_variants(monkeypatch):
    calls = []

    async def fake_search_contacts(query, *, read_mask):  # noqa: ANN001
        calls.append(query)
        return [{"resourceName": "people/1"}]

    async def fake_get_contact(resource_name, *, person_fields):  # noqa: ANN001
        assert resource_name == "people/1"
        return {
            "resourceName": resource_name,
            "etag": "etag-1",
            "phoneNumbers": [{"value": "+12345678901"}],
            "metadata": {"sources": [{"updateTime": "2024-01-01T00:00:00Z"}]},
        }

    monkeypatch.setattr("app.integrations.google_client.search_contacts", fake_search_contacts)
    monkeypatch.setattr("app.integrations.google_client.get_contact", fake_get_contact)

    keys = MatchKeys.from_raw(["+1 (234) 567-8901"], [])
    candidates = await search_google_candidates(keys)

    assert ["+12345678901", "12345678901"] == calls
    assert len(candidates) == 1
    assert candidates[0].matched_phones == {"+12345678901"}
