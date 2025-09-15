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
        return [{"resourceName": "r1", "name": "g", "emails": [], "phones": []}]

    async def fake_fetch_amo(limit):  # noqa: ARG001
        return [{"id": 1, "name": "a", "emails": [], "phones": []}]

    monkeypatch.setattr(sync_route, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/sync/contacts/dry-run?limit=10&direction=both")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["counts"] == {"google": 1, "amo": 1}


def test_dry_run_direction_amo(monkeypatch):
    from app.routes import sync as sync_route

    async def fake_fetch_amo(limit):  # noqa: ARG001
        return [{"id": 1, "name": "a", "emails": [], "phones": []}]

    async def fake_fetch_google(limit, since_days=None):  # noqa: ARG001
        raise AssertionError("fetch_google_contacts should not be called")

    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)
    monkeypatch.setattr(sync_route, "fetch_google_contacts", fake_fetch_google)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/sync/contacts/dry-run?limit=10&direction=amo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["direction"] == "amo"
        assert data["counts"] == {"google": 0, "amo": 1}
        assert data["google_sample"] == []
        assert data["amo_sample"] == [{"id": 1, "name": "a", "emails": [], "phones": []}]


def test_dry_run_direction_google(monkeypatch):
    from app.routes import sync as sync_route

    async def fake_fetch_google(limit, since_days=None):  # noqa: ARG001
        return [{"resourceName": "r1", "name": "g", "emails": [], "phones": []}]

    async def fake_fetch_amo(limit):  # noqa: ARG001
        raise AssertionError("fetch_amo_contacts should not be called")

    monkeypatch.setattr(sync_route, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/sync/contacts/dry-run?limit=10&direction=google")
        assert resp.status_code == 200
        data = resp.json()
        assert data["direction"] == "google"
        assert data["counts"] == {"google": 1, "amo": 0}
        assert data["amo_sample"] == []
        assert data["google_sample"] == [{"resourceName": "r1", "name": "g", "emails": [], "phones": []}]

