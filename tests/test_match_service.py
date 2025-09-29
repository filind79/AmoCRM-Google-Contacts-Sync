from datetime import datetime, timezone

import pytest

from app.services.match import (
    MatchCandidate,
    MatchContext,
    MatchKeys,
    choose_primary,
    search_google_candidates,
)


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


def _candidate(
    resource: str,
    *,
    in_group: bool,
    update_time: datetime,
) -> MatchCandidate:
    memberships = []
    if in_group:
        memberships = [
            {
                "contactGroupMembership": {
                    "contactGroupResourceName": "contactGroups/1"
                }
            }
        ]
    person = {
        "resourceName": resource,
        "memberships": memberships,
        "metadata": {
            "sources": [
                {"updateTime": update_time.isoformat().replace("+00:00", "Z")}
            ]
        },
    }
    return MatchCandidate(
        resource_name=resource,
        person=person,
        matched_phones={"123"},
        matched_emails=set(),
        update_time=update_time,
    )


def test_choose_primary_prefers_group_membership():
    keys = MatchKeys(phones={"123"}, emails=set())
    newer = datetime(2024, 5, 1, tzinfo=timezone.utc)
    older = datetime(2024, 4, 1, tzinfo=timezone.utc)
    candidates = [
        _candidate("people/2", in_group=False, update_time=newer),
        _candidate("people/1", in_group=True, update_time=older),
    ]
    context = MatchContext(group_resource_name="contactGroups/1")

    selected = choose_primary(candidates, keys, context)

    assert selected is not None
    assert selected.resource_name == "people/1"
