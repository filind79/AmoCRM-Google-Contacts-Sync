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
