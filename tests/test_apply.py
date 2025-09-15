from fastapi.testclient import TestClient


def create(monkeypatch, secret: str | None = None):
    from app.config import settings

    if secret is not None:
        monkeypatch.setattr(settings, "debug_secret", secret)
    from app.main import create_app

    return create_app()


def test_apply_creates_missing(monkeypatch):
    from app import sync as sync_module

    async def fake_fetch_amo(limit, since_days):  # noqa: ARG001
        return [
            {"id": 1, "name": "a", "emails": ["a@example.com"], "phones": []},
            {"id": 2, "name": "b", "emails": ["b@example.com"], "phones": []},
            {"id": 3, "name": "c", "emails": ["c@example.com"], "phones": []},
        ]

    async def fake_fetch_google(limit, since_days):  # noqa: ARG001
        return [{"resourceName": "r1", "name": "g", "emails": ["a@example.com"], "phones": []}]

    created: list[str] = []

    async def fake_create_contact(data):
        resource = f"people/{data['id']}"
        created.append(resource)
        return {"resourceName": resource}

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch_amo)
    monkeypatch.setattr(sync_module, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_module.google_people, "create_contact", fake_create_contact)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=2&direction=to_google&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 2
        assert data["skipped_existing"] == 1
        assert len(created) == 2


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

