from fastapi.testclient import TestClient


def create(monkeypatch, secret: str | None = None):
    from app.config import settings

    if secret is not None:
        monkeypatch.setattr(settings, "debug_secret", secret)
    from app.main import create_app

    return create_app()


def test_apply_upserts(monkeypatch):
    from app import sync as sync_module

    async def fake_fetch_amo(limit, since_days):  # noqa: ARG001
        return [
            {"id": 1, "name": "a", "emails": ["a@example.com"], "phones": []},
            {"id": 2, "name": "b", "emails": ["b@example.com"], "phones": []},
            {"id": 3, "name": "c", "emails": ["c@example.com"], "phones": []},
        ]

    upserts: list[tuple[int, str]] = []

    async def fake_upsert(amo_id, data):  # noqa: ARG001
        action = "update" if amo_id == 1 else "create"
        upserts.append((amo_id, action))
        return {"resourceName": f"people/{amo_id}", "action": action}

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch_amo)
    monkeypatch.setattr(sync_module.google_people, "upsert_contact_by_external_id", fake_upsert)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=3&direction=to_google&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 2
        assert data["updated"] == 1
        assert data["processed"] == 3
        assert len(upserts) == 3


def test_apply_requires_secret_and_confirm(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=1&direction=to_google&confirm=1"
        )
        assert resp.status_code == 403
        resp = client.post(
            "/sync/contacts/apply?limit=1&direction=to_google",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 403

