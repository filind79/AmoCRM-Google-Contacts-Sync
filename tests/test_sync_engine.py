from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.match import MatchCandidate, MatchKeys
from app.services.sync_engine import SyncEngine


class DummySession:
    def close(self) -> None:  # noqa: D401 - simple stub
        return None


def make_candidate(
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


@pytest.fixture
def engine_env(monkeypatch):
    from app.services import sync_engine as module

    monkeypatch.setattr(module.settings, "google_contact_group_name", "")
    monkeypatch.setattr(module.settings, "auto_merge_duplicates", True)

    links: dict[str, SimpleNamespace] = {}

    monkeypatch.setattr(module, "get_db_session", lambda: DummySession())
    monkeypatch.setattr(module, "get_link", lambda _session, amo_id: links.get(amo_id))

    def fake_save_link(_session, amo_id: str, resource: str):
        link = SimpleNamespace(google_resource_name=resource)
        links[amo_id] = link
        return link

    monkeypatch.setattr(module, "save_link", fake_save_link)
    monkeypatch.setattr(module, "remap_google_links", lambda *args, **kwargs: None)

    return module, links


@pytest.mark.asyncio
async def test_plan_skips_invalid_keys(monkeypatch, engine_env):
    module, _ = engine_env
    engine = SyncEngine()
    try:
        plan = await engine.plan({"id": 1, "name": "Invalid", "phones": ["abc"], "emails": []})
    finally:
        engine.close()
    assert plan.action == "skip"
    assert plan.reason == "no_valid_keys"


@pytest.mark.asyncio
async def test_single_candidate_updates(monkeypatch, engine_env):
    module, links = engine_env

    keys = MatchKeys.from_raw(["+12345678901"], [])
    candidate = make_candidate(
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
        return {"resourceName": resource_name}

    monkeypatch.setattr(module, "search_google_candidates", fake_search)
    monkeypatch.setattr(module.google_client, "update_contact", fake_update)

    engine = SyncEngine()
    try:
        plan = await engine.plan({"id": 1, "name": "Alice", "phones": ["+12345678901"], "emails": []})
        assert plan.action == "update"
        result = await engine.apply(plan)
    finally:
        engine.close()

    assert result.action == "updated"
    assert result.resource_name == "people/1"
    assert links["1"].google_resource_name == "people/1"
    assert update_calls


@pytest.mark.asyncio
async def test_multiple_candidates_merge(monkeypatch, engine_env):
    module, links = engine_env

    primary = make_candidate(
        "people/primary",
        phones=["+15551234567"],
        emails=["a@example.com"],
        amo_id="5",
        updated=datetime(2024, 2, 1, tzinfo=timezone.utc),
    )
    duplicate = make_candidate(
        "people/duplicate",
        phones=["+15551234567"],
        emails=["b@example.com"],
        amo_id=None,
        updated=datetime(2024, 1, 10, tzinfo=timezone.utc),
    )

    async def fake_search(_keys):  # noqa: ANN001
        return [primary, duplicate]

    async def fake_merge(primary_candidate, duplicates, *, keys, group_resource_name, db_session):  # noqa: ANN001
        assert duplicates
        merged = dict(primary_candidate.person)
        merged["emailAddresses"] = [
            *(primary_candidate.person.get("emailAddresses") or []),
            *(duplicates[0].person.get("emailAddresses") or []),
        ]
        return MatchCandidate(
            resource_name=primary_candidate.resource_name,
            person=merged,
            matched_phones=primary_candidate.matched_phones,
            matched_emails=primary_candidate.matched_emails,
            update_time=primary_candidate.update_time,
        )

    monkeypatch.setattr(module, "search_google_candidates", fake_search)
    monkeypatch.setattr(module, "merge_contacts", fake_merge)

    engine = SyncEngine()
    try:
        plan = await engine.plan({"id": 5, "name": "Bob", "phones": ["+1 555 123 45 67"], "emails": ["a@example.com"]})
        assert plan.action == "merge"
        result = await engine.apply(plan)
    finally:
        engine.close()

    assert result.action == "merged"
    assert result.primary == "people/primary"
    assert links["5"].google_resource_name == "people/primary"


@pytest.mark.asyncio
async def test_race_after_create_triggers_merge(monkeypatch, engine_env, caplog):
    module, links = engine_env

    caplog.set_level("INFO")

    existing = make_candidate(
        "people/existing",
        phones=["+79991234567"],
        emails=["old@example.com"],
        amo_id="42",
        updated=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    newly_created = make_candidate(
        "people/new",
        phones=["+79991234567"],
        emails=["race@example.com"],
        amo_id="42",
        updated=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    call_count = 0

    async def fake_search(_keys):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return []
        return [existing, newly_created]

    async def fake_create(payload):  # noqa: ANN001
        return {"resourceName": "people/new"}

    async def fake_merge(primary_candidate, duplicates, *, keys, group_resource_name, db_session):  # noqa: ANN001
        return MatchCandidate(
            resource_name=existing.resource_name,
            person=existing.person,
            matched_phones=existing.matched_phones,
            matched_emails=existing.matched_emails,
            update_time=existing.update_time,
        )

    async def fake_get(resource_name, *, person_fields):  # noqa: ANN001
        if resource_name == "people/new":
            return newly_created.person
        return existing.person

    monkeypatch.setattr(module, "search_google_candidates", fake_search)
    monkeypatch.setattr(module, "create_contact", fake_create)
    monkeypatch.setattr(module, "merge_contacts", fake_merge)
    monkeypatch.setattr(module.google_client, "get_contact", fake_get)

    engine = SyncEngine()
    try:
        plan = await engine.plan(
            {
                "id": 42,
                "name": "Race",
                "phones": ["+7 (999) 123-45-67"],
                "emails": ["race@example.com"],
            }
        )
        assert plan.action == "create"
        result = await engine.apply(plan)
    finally:
        engine.close()

    assert result.action == "created"
    assert result.resource_name == "people/existing"
    assert links["42"].google_resource_name == "people/existing"
    assert "postcreate.merge_performed" in caplog.text


@pytest.mark.asyncio
async def test_update_retries_on_missing_contact(monkeypatch, engine_env):
    module, _ = engine_env

    candidate = make_candidate(
        "people/missing",
        phones=["+491234567890"],
        emails=[],
        amo_id="77",
        updated=datetime(2024, 3, 1, tzinfo=timezone.utc),
    )

    async def fake_search(_keys):  # noqa: ANN001
        return [candidate]

    calls: list[str] = []

    class RetryError(Exception):
        pass

    async def fake_update(resource_name, payload, *, update_person_fields, etag):  # noqa: ANN001
        calls.append(resource_name)
        raise module.httpx.HTTPStatusError(
            "gone",
            request=None,
            response=SimpleNamespace(status_code=404),
        )

    monkeypatch.setattr(module, "search_google_candidates", fake_search)
    monkeypatch.setattr(module.google_client, "update_contact", fake_update)

    engine = SyncEngine()
    try:
        plan = await engine.plan({"id": 77, "name": "Retry", "phones": ["+49 123 456 7890"], "emails": []})
        with pytest.raises(module.RecoverableSyncError):
            await engine.apply(plan)
    finally:
        engine.close()

    assert calls == ["people/missing"] * 4
