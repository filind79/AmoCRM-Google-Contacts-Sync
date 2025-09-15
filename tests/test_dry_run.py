import logging
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker


class DummyResponse:
    def __init__(self, data=None, status_code=200):
        self._data = data or {}
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception("error")


def make_client(monkeypatch, tmp_path):
    from app.config import settings
    from app.main import create_app
    import app.storage as storage

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(settings, "db_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(settings, "debug_secret", "s")
    storage.engine = None
    storage.SessionLocal = sessionmaker(autocommit=False, autoflush=False)
    app = create_app()
    storage.init_db()
    return TestClient(app)


def patch_fetch(monkeypatch, google_people):
    async def fake_fetch_amo(limit):
        return [{} for _ in range(limit)]

    async def fake_fetch_google(limit):
        await google_people.get_access_token()
        return [{} for _ in range(limit)]

    monkeypatch.setattr("app.routes.sync.fetch_amo_contacts", fake_fetch_amo)
    monkeypatch.setattr("app.routes.sync.fetch_google_contacts", fake_fetch_google)


def test_dry_run_with_valid_token(monkeypatch, tmp_path):
    from app.storage import get_session, save_token
    import app.storage as storage
    import app.google_people as google_people

    client = make_client(monkeypatch, tmp_path)
    patch_fetch(monkeypatch, google_people)
    session = get_session()
    save_token(session, "google", "t", "r", datetime.utcnow() + timedelta(hours=1), "")
    session.close()
    resp = client.get(
        "/sync/contacts/dry-run?limit=2&direction=both",
        headers={"X-Debug-Secret": "s"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["google_read"]["count"] == 2
    assert data["amo_read"]["count"] == 2


def test_dry_run_refreshes_token(monkeypatch, tmp_path, caplog):
    from app.storage import get_session, save_token
    import app.storage as storage
    import app.google_people as google_people

    client = make_client(monkeypatch, tmp_path)
    patch_fetch(monkeypatch, google_people)
    session = get_session()
    save_token(session, "google", "old", "refresh", datetime.utcnow() - timedelta(seconds=1), "")
    session.close()

    dummy = DummyResponse({"access_token": "new", "expires_in": 3600})

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def post(self, url, data=None):
            return dummy

    monkeypatch.setattr(
        google_people,
        "httpx",
        type("X", (), {"AsyncClient": lambda *a, **k: DummyClient()}),
    )

    with caplog.at_level(logging.INFO):
        resp = client.get("/sync/contacts/dry-run", headers={"X-Debug-Secret": "s"})
    assert resp.status_code == 200
    assert "Google token refreshed" in caplog.text
    session = get_session()
    token = session.query(storage.Token).filter_by(system="google").first()
    assert token.access_token == "new"
    session.close()


def test_dry_run_missing_token(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    import app.google_people as google_people
    patch_fetch(monkeypatch, google_people)
    monkeypatch.setattr(google_people, "get_token", lambda session, system: None)
    resp = client.get("/sync/contacts/dry-run", headers={"X-Debug-Secret": "s"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "google token missing"


def test_dry_run_refresh_failure(monkeypatch, tmp_path):
    from app.storage import get_session, save_token
    import app.storage as storage
    import app.google_people as google_people

    client = make_client(monkeypatch, tmp_path)
    patch_fetch(monkeypatch, google_people)
    session = get_session()
    save_token(session, "google", "old", "refresh", datetime.utcnow() - timedelta(seconds=1), "")
    session.close()

    dummy = DummyResponse(status_code=400)

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def post(self, url, data=None):
            return dummy

    monkeypatch.setattr(
        google_people,
        "httpx",
        type("X", (), {"AsyncClient": lambda *a, **k: DummyClient()}),
    )

    resp = client.get("/sync/contacts/dry-run", headers={"X-Debug-Secret": "s"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "google token refresh failed"
