from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.match import MatchCandidate, MatchKeys
from app.services.sync_apply import GoogleApplyService


class DummySession:
    def close(self) -> None:  # noqa: D401 - simple stub
        return None


@pytest.fixture
def apply_service_env(monkeypatch):
    from app.services import sync_apply as module

    monkeypatch.setattr(module.settings, "google_contact_group_name", "")

    links: dict[str, SimpleNamespace] = {}

    monkeypatch.setattr(module, "get_db_session", lambda: DummySession())
    monkeypatch.setattr(module, "get_link", lambda _session, amo_id: links.get(amo_id))

    def fake_save_link(_session, amo_id: str, resource: str):
        link = SimpleNamespace(google_resource_name=resource)
        links[amo_id] = link
        return link

    monkeypatch.setattr(module, "save_link", fake_save_link)

    return module, links


def _candidate(
    resource_name: str,
    *,
    phones: list[str],
    emails: list[str],
    amo_id: str | None,
    updated: datetime,
) -> MatchCandidate:
    person = {
        "resourceName": resource_name,
        "etag": f"etag-{resource_name}",
        "phoneNumbers": [{"value": p} for p in phones],
        "emailAddresses": [{"value": e} for e in emails],
        "metadata": {"sources": [{"updateTime": updated.isoformat().replace("+00:00", "Z")}]},
    }
    if amo_id is not None:
        person["externalIds"] = [{"value": amo_id, "type": "amo_id"}]
    return MatchCandidate(
        resource_name=resource_name,
        person=person,
        matched_phones=set(phones),
        matched_emails=set(emails),
        update_time=updated,
    )


@pytest.mark.asyncio
async def test_process_contact_updates_existing(monkeypatch, apply_service_env):
    module, links = apply_service_env

    keys = MatchKeys.from_raw(["+12345678901"], [])
    candidate = _candidate(
        "people/1",
        phones=["+12345678901"],
        emails=[],
        amo_id="1",
        updated=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    async def fake_search(_keys):  # noqa: ANN001
        assert _keys.phones == keys.phones
        return [candidate]

    update_calls: list[tuple[str, dict[str, object]]] = []

    async def fake_update(resource_name, payload, *, update_person_fields, etag):  # noqa: ANN001
        update_calls.append((resource_name, payload))
        assert etag == candidate.person["etag"]
        assert "emailAddresses" not in payload or payload["emailAddresses"]
        assert update_person_fields
        return {"resourceName": resource_name}

    create_calls: list[dict[str, object]] = []

    async def fake_create(payload):  # noqa: ANN001
        create_calls.append(payload)
        return {"resourceName": "people/new"}

    def fail_merge(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("merge should not be called")

    monkeypatch.setattr(module, "search_google_candidates", fake_search)
    monkeypatch.setattr(module.google_client, "update_contact", fake_update)
    monkeypatch.setattr(module, "create_contact", fake_create)
    monkeypatch.setattr(module, "merge_contacts", fail_merge)

    service = GoogleApplyService()
    contact = {"id": 1, "name": "Alice", "phones": ["+12345678901"], "emails": []}
    result = await service.process_contact(contact)
    service.close()

    assert result.action == "updated"
    assert result.resource_name == "people/1"
    assert links["1"].google_resource_name == "people/1"
    assert update_calls
    assert not create_calls


@pytest.mark.asyncio
async def test_process_contact_postcreate_race_merges_duplicates(
    monkeypatch, apply_service_env, caplog
):
    module, links = apply_service_env

    caplog.set_level("INFO")

    call_count = 0

    keys = MatchKeys.from_raw(["+79991234567"], ["race@example.com"])
    existing = _candidate(
        "people/existing",
        phones=["+79991234567"],
        emails=["old@example.com"],
        amo_id="5",
        updated=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    newly_created = _candidate(
        "people/new",
        phones=["+79991234567"],
        emails=["race@example.com"],
        amo_id="5",
        updated=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    extra = _candidate(
        "people/extra",
        phones=["+79991234567"],
        emails=["third@example.com"],
        amo_id=None,
        updated=datetime(2023, 12, 31, tzinfo=timezone.utc),
    )

    async def fake_search(_keys):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return []
        return [existing, newly_created, extra]

    async def fake_create(payload):  # noqa: ANN001
        return {"resourceName": "people/new"}

    merge_calls: list[tuple[MatchCandidate, list[MatchCandidate]]] = []

    async def fake_merge(primary, duplicates, *, keys, group_resource_name, db_session):  # noqa: ANN001
        merge_calls.append((primary, list(duplicates)))
        merged_person = dict(primary.person)
        extra_phones = [
            phone
            for dup in duplicates
            for phone in dup.person.get("phoneNumbers") or []
        ]
        extra_emails = [
            email
            for dup in duplicates
            for email in dup.person.get("emailAddresses") or []
        ]
        merged_person["phoneNumbers"] = [
            *(primary.person.get("phoneNumbers") or []),
            *extra_phones,
        ]
        merged_person["emailAddresses"] = [
            *(primary.person.get("emailAddresses") or []),
            *extra_emails,
        ]
        return MatchCandidate(
            resource_name=primary.resource_name,
            person=merged_person,
            matched_phones=primary.matched_phones,
            matched_emails=primary.matched_emails,
            update_time=primary.update_time,
        )

    async def fake_get_contact(resource_name, *, person_fields):  # noqa: ANN001
        assert person_fields == module.PERSON_FIELDS
        if resource_name == "people/new":
            return newly_created.person
        if resource_name == "people/existing":
            return existing.person
        return extra.person

    monkeypatch.setattr(module, "search_google_candidates", fake_search)
    monkeypatch.setattr(module, "create_contact", fake_create)
    monkeypatch.setattr(module, "merge_contacts", fake_merge)
    monkeypatch.setattr(module.google_client, "get_contact", fake_get_contact)

    service = GoogleApplyService()
    contact = {
        "id": 5,
        "name": "Race",
        "phones": ["+7 (999) 123-45-67"],
        "emails": ["race@example.com"],
    }
    result = await service.process_contact(contact)
    service.close()

    assert result.action == "created"
    assert result.resource_name == "people/existing"
    assert links["5"].google_resource_name == "people/existing"
    assert call_count == 3
    assert merge_calls
    primary_call, duplicates_call = merge_calls[0]
    assert primary_call.resource_name == "people/existing"
    duplicate_names = sorted(c.resource_name for c in duplicates_call)
    assert duplicate_names == ["people/extra", "people/new"]
    assert any(
        record.message == "postcreate.merge_performed"
        and getattr(record, "primary", None) == "people/existing"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_process_contact_skips_invalid_keys(monkeypatch, apply_service_env):
    module, _ = apply_service_env

    async def fail_search(_):  # noqa: ANN001
        raise AssertionError("search should not be called")

    async def fail_create(_):  # noqa: ANN001
        raise AssertionError("create should not be called")

    monkeypatch.setattr(module, "search_google_candidates", fail_search)
    monkeypatch.setattr(module, "create_contact", fail_create)

    service = GoogleApplyService()
    contact = {"id": 10, "name": "Invalid", "phones": ["abc"], "emails": ["" ]}
    result = await service.process_contact(contact)
    service.close()

    assert result.action == "skipped_invalid_phone"
    assert result.resource_name is None
