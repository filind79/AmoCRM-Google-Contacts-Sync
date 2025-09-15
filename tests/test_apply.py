from fastapi.testclient import TestClient
from app import amocrm
from app.google_auth import GoogleAuthError


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

    async def fake_fetch_google(limit, since_days, amo_contacts, list_existing):  # noqa: ARG001
        items = []
        for c in amo_contacts:
            if "a@example.com" in c.get("emails", []):
                items.append(
                    {
                        "resourceName": "people/1",
                        "name": "old",
                        "emails": ["a@example.com"],
                        "phones": [],
                        "etag": "e1",
                    }
                )
            if "b@example.com" in c.get("emails", []):
                items.append(
                    {
                        "resourceName": "people/2",
                        "name": "b",
                        "emails": ["b@example.com"],
                        "phones": [],
                        "etag": "e2",
                    }
                )
        return items, {}

    updates: list[tuple[str, str, dict]] = []

    async def fake_update(resource_name, etag, data):
        updates.append((resource_name, etag, data))
        return {}

    creates: list[int] = []

    async def fake_create(data):
        creates.append(data["external_id"])
        return {"resourceName": f"people/{data['external_id']}"}

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch_amo)
    monkeypatch.setattr(sync_module, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_module.google_people, "update_contact", fake_update)
    monkeypatch.setattr(sync_module.google_people, "create_contact", fake_create)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=3&direction=to_google&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["updated"] == 1
        assert data["skip_existing"] == 1
        assert data["processed"] == 3
        assert creates == [3]
        assert [u[0] for u in updates] == ["people/1"]
        assert updates[0][1] == "e1"
        assert updates[0][2] == {"name": "a"}


def test_apply_missing_etag(monkeypatch):
    from app import sync as sync_module

    async def fake_fetch_amo(limit, since_days):  # noqa: ARG001
        return [{"id": 1, "name": "a", "emails": ["a@example.com"], "phones": []}]

    async def fake_fetch_google(limit, since_days, amo_contacts, list_existing):  # noqa: ARG001
        return (
            [
                {
                    "resourceName": "people/1",
                    "name": "old",
                    "emails": ["a@example.com"],
                    "phones": [],
                    "etag": None,
                }
            ],
            {},
        )

    updates: list[tuple[str, str, dict]] = []

    async def fake_update(resource_name, etag, data):  # pragma: no cover - should not run
        updates.append((resource_name, etag, data))
        return {}

    async def fake_create(data):  # pragma: no cover - should not run
        return {"resourceName": f"people/{data['external_id']}"}

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch_amo)
    monkeypatch.setattr(sync_module, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_module.google_people, "update_contact", fake_update)
    monkeypatch.setattr(sync_module.google_people, "create_contact", fake_create)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=1&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 0
        assert updates == []
        assert data["errors"][0]["reason"] == "missing_etag"


def test_apply_forbidden_without_secret_or_confirm(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=1&direction=to_google&confirm=1",
        )
        assert resp.status_code == 403
        resp = client.post(
            "/sync/contacts/apply?limit=1&direction=to_google",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 403


def test_apply_invalid_direction(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=1&direction=both&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 400


def test_apply_google_auth_error_to_401(monkeypatch):
    from app.routes import sync as sync_routes

    async def fake_apply(limit, since_days):  # noqa: ARG001
        raise GoogleAuthError("no_token")

    monkeypatch.setattr(sync_routes, "apply_contacts_to_google", fake_apply)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 401
        assert resp.json() == {
            "detail": "Google auth required",
            "auth_url": "/auth/google/start",
        }


def test_apply_generic_error_to_502(monkeypatch):
    from app.routes import sync as sync_routes

    async def fake_apply(limit, since_days):  # noqa: ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr(sync_routes, "apply_contacts_to_google", fake_apply)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 502
        assert resp.json() == {"detail": "Apply failed: boom"}


def test_apply_success_passthrough(monkeypatch):
    from app.routes import sync as sync_routes

    async def fake_apply(limit, since_days):  # noqa: ARG001
        return {"status": "ok", "created": 3, "skipped": 2}

    monkeypatch.setattr(sync_routes, "apply_contacts_to_google", fake_apply)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "created": 3, "skipped": 2}


def test_apply_skips_none_custom_fields_contacts_no_crash(monkeypatch):
    from app import sync as sync_module

    async def fake_fetch(limit, since_days):  # noqa: ARG001
        raw = [{"id": 1, "name": "", "custom_fields_values": None}]
        parsed = []
        for c in raw:
            fields = amocrm.extract_name_and_fields(c)
            parsed.append({"id": c["id"], "name": fields["name"], "emails": fields["emails"], "phones": fields["phones"]})
        return parsed

    async def fake_upsert(amo_id, data):  # noqa: ARG001
        return {"resourceName": f"people/{amo_id}", "action": "create"}

    async def fake_fetch_google(limit, since_days, amo_contacts, list_existing):  # noqa: ARG001
        return ([], {})

    created: list[int] = []

    async def fake_create(data):
        created.append(data["external_id"])
        return {"resourceName": f"people/{data['external_id']}"}

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch)
    monkeypatch.setattr(sync_module, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_module.google_people, "create_contact", fake_create)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert created == [1]

