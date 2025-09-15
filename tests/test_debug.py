import importlib

from fastapi.testclient import TestClient


def _app_with_secret(monkeypatch, secret: str):
    from app.config import settings
    monkeypatch.setattr(settings, "debug_secret", secret)
    from app.main import create_app
    return create_app()


def test_debug_not_mounted_without_secret(monkeypatch):
    app = _app_with_secret(monkeypatch, "")
    client = TestClient(app)
    resp = client.get("/debug/ping")
    assert resp.status_code == 404


def test_debug_requires_header(monkeypatch):
    app = _app_with_secret(monkeypatch, "s")
    client = TestClient(app)
    resp = client.get("/debug/ping")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid debug secret"}


def test_debug_with_header(monkeypatch):
    app = _app_with_secret(monkeypatch, "s")
    client = TestClient(app)
    resp = client.get("/debug/ping", headers={"X-Debug-Secret": "s"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_debug_ping_env(monkeypatch):
    monkeypatch.setenv("DEBUG_SECRET", "env")
    import app.config as config
    importlib.reload(config)
    import app.debug as debug
    importlib.reload(debug)
    import app.main as main
    importlib.reload(main)
    client = TestClient(main.app)
    resp = client.get("/debug/ping", headers={"X-Debug-Secret": "env"})
    assert resp.status_code == 200


def test_debug_db(monkeypatch):
    app = _app_with_secret(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.get("/debug/db", headers={"X-Debug-Secret": "s"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["dialect"] == "sqlite"
        assert data["ok"] is True


def test_debug_google_no_token(monkeypatch):
    app = _app_with_secret(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.get("/debug/google", headers={"X-Debug-Secret": "s"})
        assert resp.status_code == 200
        assert resp.json() == {
            "has_token": False,
            "expires_at": None,
            "will_refresh": False,
        }


def test_debug_google_with_token(monkeypatch):
    app = _app_with_secret(monkeypatch, "s")
    from datetime import datetime, timedelta
    from app.storage import get_session, save_token

    session = get_session()
    expiry = datetime.utcnow().replace(microsecond=0) - timedelta(minutes=1)
    save_token(session, "google", access_token="a", refresh_token="r", expiry=expiry, scopes="")
    session.close()

    with TestClient(app) as client:
        resp = client.get("/debug/google", headers={"X-Debug-Secret": "s"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_token"] is True
        assert data["will_refresh"] is True
        assert data["expires_at"].startswith(expiry.isoformat())


def test_debug_amo(monkeypatch):
    app = _app_with_secret(monkeypatch, "s")
    from app.config import settings
    with TestClient(app) as client:
        resp = client.get("/debug/amo", headers={"X-Debug-Secret": "s"})
        assert resp.status_code == 200
        assert resp.json() == {"has_token": False, "base_url": settings.amo_base_url}
