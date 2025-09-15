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

    upserts: list[tuple[int, str]] = []

    async def fake_upsert(amo_id, data):  # noqa: ARG001
        action = "update" if amo_id == 1 else "create"
        upserts.append((amo_id, action))
        return {"resourceName": f"people/{amo_id}", "action": action}

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch_amo)
    monkeypatch.setattr(
        sync_module.google_people, "upsert_contact_by_external_id", fake_upsert
    )

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

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch)
    monkeypatch.setattr(
        sync_module.google_people, "upsert_contact_by_external_id", fake_upsert
    )

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1

