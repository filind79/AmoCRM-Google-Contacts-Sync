import pytest
from types import SimpleNamespace
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import create_app
from app.pending_sync_worker import pending_sync_worker
from app.storage import PendingSync, get_session, init_db


@pytest.mark.asyncio
async def test_webhook_requires_secret(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "secret")
    monkeypatch.setattr(settings, "debug_secret", "debug")
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/amo", json={"contact_id": 1})
        assert resp.status_code == 401
        assert resp.json() == {
            "detail": "Unauthorized",
            "accepted": ["X-Webhook-Secret", "X-Debug-Secret", "?token"],
        }


@pytest.mark.asyncio
async def test_webhook_enqueues_and_processes(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "secret")
    monkeypatch.setattr(settings, "debug_secret", "")
    init_db()
    session = get_session()
    try:
        session.query(PendingSync).delete()
        session.commit()
    finally:
        session.close()

    processed: list[int] = []

    async def fake_get_contact(cid: int):  # noqa: D401
        return {"id": cid, "name": "Test", "custom_fields_values": []}

    def fake_extract(contact):  # noqa: D401
        return {"name": contact.get("name"), "emails": [], "phones": []}

    class DummyEngine:
        def __init__(self):
            pass

        async def plan(self, payload):  # noqa: D401
            return SimpleNamespace(contact=payload)

        async def apply(self, plan):  # noqa: D401
            amo_contact_id = plan.contact["id"]
            processed.append(amo_contact_id)
            return SimpleNamespace(action="updated", resource_name=f"people/{amo_contact_id}")

        def close(self):  # noqa: D401
            return None

    monkeypatch.setattr("app.pending_sync_worker.get_contact", fake_get_contact)
    monkeypatch.setattr("app.pending_sync_worker.extract_name_and_fields", fake_extract)
    monkeypatch.setattr("app.pending_sync_worker.SyncEngine", DummyEngine)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook/amo?token=secret",
            json={"event": "contact_updated", "contact_id": 123},
        )
        assert resp.status_code == 200
        assert resp.json()["queued"] == [123]
        await pending_sync_worker.drain()

    assert processed == [123]

    session = get_session()
    try:
        remaining = session.query(PendingSync).count()
    finally:
        session.close()
    assert remaining == 0


@pytest.mark.asyncio
async def test_webhook_supports_legacy_payload(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "secret")
    monkeypatch.setattr(settings, "debug_secret", "")

    collected: list[int] = []

    async def fake_get_contact(cid: int):  # noqa: D401
        return {"id": cid, "name": "Legacy", "custom_fields_values": []}

    def fake_extract(contact):  # noqa: D401
        return {"name": contact.get("name"), "emails": [], "phones": []}

    class DummyEngine:
        def __init__(self):
            pass

        async def plan(self, payload):  # noqa: D401
            return SimpleNamespace(contact=payload)

        async def apply(self, plan):  # noqa: D401
            amo_contact_id = plan.contact["id"]
            collected.append(amo_contact_id)
            return SimpleNamespace(action="created", resource_name=f"people/{amo_contact_id}")

        def close(self):  # noqa: D401
            return None

    monkeypatch.setattr("app.pending_sync_worker.get_contact", fake_get_contact)
    monkeypatch.setattr("app.pending_sync_worker.extract_name_and_fields", fake_extract)
    monkeypatch.setattr("app.pending_sync_worker.SyncEngine", DummyEngine)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook/amo?token=secret",
            json={"contacts": {"update": [{"id": 5}, {"id": "6"}]}},
        )
        assert resp.status_code == 200
        assert set(resp.json()["queued"]) == {5, 6}
        await pending_sync_worker.drain()

    assert set(collected) == {5, 6}


@pytest.mark.asyncio
async def test_webhook_accepts_debug_secret(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "secret")
    monkeypatch.setattr(settings, "debug_secret", "debug")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook/amo",
            json={"event": "contact_updated", "contact_id": 321},
            headers={"X-Debug-Secret": "debug"},
        )
        assert resp.status_code == 200
        assert resp.json()["queued"] == [321]


@pytest.mark.asyncio
async def test_webhook_parses_json_payload(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "secret")
    monkeypatch.setattr(settings, "debug_secret", "")

    captured: list[int] = []

    def fake_enqueue(cid: int) -> None:
        captured.append(cid)

    monkeypatch.setattr("app.webhooks.enqueue_contact", fake_enqueue)
    monkeypatch.setattr("app.webhooks.pending_sync_worker.wake", lambda: None)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook/amo?token=secret",
            json={"event": "contact_updated", "contact_id": 90959743},
        )

    assert resp.status_code == 200
    assert resp.json() == {"queued": [90959743]}
    assert captured == [90959743]


@pytest.mark.asyncio
async def test_webhook_parses_form_payload(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "secret")
    monkeypatch.setattr(settings, "debug_secret", "")

    captured: set[int] = set()

    def fake_enqueue(cid: int) -> None:
        captured.add(cid)

    monkeypatch.setattr("app.webhooks.enqueue_contact", fake_enqueue)
    monkeypatch.setattr("app.webhooks.pending_sync_worker.wake", lambda: None)

    payload = (
        "contacts[add][0][id]=101&contacts[add][0][name]=Test&"
        "contacts[update][0][id]=202&contacts[update][1][id]=203"
    )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook/amo?token=secret",
            content=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert resp.status_code == 200
    assert set(resp.json()["queued"]) == {101, 202, 203}
    assert captured == {101, 202, 203}


@pytest.mark.asyncio
async def test_webhook_ignores_invalid_form_ids(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "secret")
    monkeypatch.setattr(settings, "debug_secret", "")

    captured: list[int] = []

    def fake_enqueue(cid: int) -> None:
        captured.append(cid)

    monkeypatch.setattr("app.webhooks.enqueue_contact", fake_enqueue)
    monkeypatch.setattr("app.webhooks.pending_sync_worker.wake", lambda: None)

    payload = (
        "contacts[add][0][id]=abc&contacts[add][1][id]=303&"
        "contacts[update][0][id]=&contacts[update][1][id]=404"
    )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook/amo?token=secret",
            content=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert resp.status_code == 200
    assert set(resp.json()["queued"]) == {303, 404}
    assert set(captured) == {303, 404}


@pytest.mark.asyncio
async def test_webhook_empty_payload_returns_warning(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "secret")
    monkeypatch.setattr(settings, "debug_secret", "")

    monkeypatch.setattr("app.webhooks.enqueue_contact", lambda cid: None)
    monkeypatch.setattr("app.webhooks.pending_sync_worker.wake", lambda: None)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook/amo?token=secret",
            json={"event": "contact_updated"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"queued": [], "warning": "no_contact_ids_parsed"}
