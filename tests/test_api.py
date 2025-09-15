from datetime import datetime, timezone

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

    async def fake_fetch_google(limit, since_days=None, amo_contacts=None, list_existing=True):  # noqa: ARG001
        return (
            [{"resourceName": "r1", "name": "g", "emails": ["g@ex.com"], "phones": []}],
            {"requests": 1, "considered": 1, "found": 0},
        )

    async def fake_fetch_amo(limit):  # noqa: ARG001
        return [{"id": 1, "name": "a", "emails": ["a@ex.com"], "phones": []}]

    monkeypatch.setattr(sync_route, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/sync/contacts/dry-run?limit=10&direction=both")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["summary"]["actions"]["amo_to_google"]["create"] == 1
        assert data["summary"]["actions"]["google_to_amo"]["create"] == 1
        assert len(data["samples"]["amo_only"]) == 1
        assert len(data["samples"]["google_only"]) == 1
        assert data["debug"]["counters"]["requests"] == 1


def test_dry_run_direction_amo(monkeypatch):
    from app.routes import sync as sync_route

    async def fake_fetch_amo(limit):  # noqa: ARG001
        return [{"id": 1, "name": "a", "emails": ["a@ex.com"], "phones": []}]

    async def fake_fetch_google(limit, since_days=None, amo_contacts=None, list_existing=True):  # noqa: ARG001
        return ([], {"requests": 0, "considered": 0, "found": 0})

    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)
    monkeypatch.setattr(sync_route, "fetch_google_contacts", fake_fetch_google)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/sync/contacts/dry-run?limit=10&direction=amo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["amo"]["fetched"] == 1
        assert data["summary"]["google"]["fetched"] == 0
        assert data["summary"]["actions"] == {"amo_to_google": {"create": 1, "update": 0}}
        assert len(data["samples"]["amo_only"]) == 1
        assert "google_only" not in data["samples"]
        assert data["debug"]["counters"]["requests"] == 0


def test_dry_run_direction_google(monkeypatch):
    from app.routes import sync as sync_route

    async def fake_fetch_google(limit, since_days=None, amo_contacts=None, list_existing=True):  # noqa: ARG001
        return (
            [{"resourceName": "r1", "name": "g", "emails": ["g@ex.com"], "phones": []}],
            {"requests": 1, "considered": 1, "found": 0},
        )

    async def fake_fetch_amo(limit):  # noqa: ARG001
        return []

    monkeypatch.setattr(sync_route, "fetch_google_contacts", fake_fetch_google)
    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/sync/contacts/dry-run?limit=10&direction=google")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["google"]["fetched"] == 1
        assert data["summary"]["amo"]["fetched"] == 0
        assert data["summary"]["actions"] == {"google_to_amo": {"create": 1, "update": 0}}
        assert len(data["samples"]["google_only"]) == 1
        assert "amo_only" not in data["samples"]
        assert data["debug"]["counters"]["requests"] == 1


def test_dry_run_since_days(monkeypatch):
    from app.routes import sync as sync_route
    from app import google_people
    from app.google_people import Contact

    async def fake_list_contacts(limit, since_days=None, counters=None):  # noqa: ARG001
        return [
            Contact(
                resource_id="r1",
                name="g",
                email="g@ex.com",
                phone=None,
                update_time=datetime.now(timezone.utc),
            )
        ]

    async def fake_search_contacts(query, counters=None):  # noqa: ARG001
        return []

    async def fake_fetch_amo(limit, since_days=None):  # noqa: ARG001
        return []

    monkeypatch.setattr(google_people, "list_contacts", fake_list_contacts)
    monkeypatch.setattr(google_people, "search_contacts", fake_search_contacts)
    monkeypatch.setattr(sync_route, "fetch_amo_contacts", fake_fetch_amo)

    app = create(monkeypatch)
    with TestClient(app) as client:
        resp = client.get(
            "/sync/contacts/dry-run?direction=both&limit=5&since_days=30"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] is not None

