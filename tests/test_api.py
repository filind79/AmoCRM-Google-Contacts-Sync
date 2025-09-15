from fastapi.testclient import TestClient


def create(monkeypatch, secret: str | None = None):
    from app.config import settings

    if secret is not None:
        monkeypatch.setattr(settings, "debug_secret", secret)
    from app.main import create_app

    return create_app()


def test_debug_db(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.get("/debug/db", headers={"X-Debug-Secret": "s"})
        assert resp.status_code == 200
        assert resp.json()["db"] == "ok"


def test_dry_run_no_token(monkeypatch):
    from app import storage as storage
    from app.routes import sync as sync_route

    storage.init_db()
    sess = storage.get_session()
    sess.query(storage.Token).delete()
    sess.commit()
    sess.close()

    async def fake_fetch_amo(limit):  # noqa: ARG001
        return []

    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/sync/contacts/dry-run?limit=10&direction=both")
        assert resp.status_code == 401
        assert resp.json()["auth_url"] == "/auth/google/start"


def test_dry_run_ok(monkeypatch):
    from app.routes import sync as sync_route

    async def fake_fetch_google(limit, since_days=None):  # noqa: ARG001
        return [{"resourceName": "r1", "name": "g", "emails": ["g@example.com"], "phones": []}]

    async def fake_fetch_amo(limit):  # noqa: ARG001
        return [{"id": 1, "name": "a", "emails": ["a@example.com"], "phones": []}]

    monkeypatch.setattr(sync_route, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/sync/contacts/dry-run?limit=10&direction=both")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["counts"] == {"google": 1, "amo": 1}
        assert data["summary"] == {
            "to_google": {"create": 1, "update": 0, "skip_existing": 0},
            "to_amo": {"create": 1, "update": 0, "skip_existing": 0},
        }
        assert len(data["samples"]["to_google_create"]) == 1
        assert len(data["samples"]["to_amo_create"]) == 1


def test_apply_requires_secret_and_confirm(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        url = "/sync/contacts/apply?limit=10&direction=to_google&confirm=1"
        resp = client.post(url, headers={"X-Debug-Secret": "bad"})
        assert resp.status_code == 403
        resp = client.post(
            url.replace("confirm=1", "confirm=0"), headers={"X-Debug-Secret": "s"}
        )
        assert resp.status_code == 403


def test_apply_bad_direction(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=10&direction=amo&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 400


def test_apply_google_auth(monkeypatch):
    from app.google_auth import GoogleAuthError
    from app.routes import sync as sync_route

    async def fake_apply(limit, since_days=None):  # noqa: ARG001
        raise GoogleAuthError("token_missing", "/auth/google/start")

    monkeypatch.setattr(sync_route, "apply_contacts_to_google", fake_apply)
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=10&direction=to_google&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 401
        assert resp.json()["auth_url"] == "/auth/google/start"


def test_apply_ok(monkeypatch):
    from app.routes import sync as sync_route

    async def fake_apply(limit, since_days=None):  # noqa: ARG001
        return {"applied": 1}

    monkeypatch.setattr(sync_route, "apply_contacts_to_google", fake_apply)
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=10&direction=to_google&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

