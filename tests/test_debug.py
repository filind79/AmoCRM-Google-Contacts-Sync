from fastapi.testclient import TestClient


def _create_app(monkeypatch, secret: str):
    from app.config import settings

    monkeypatch.setattr(settings, "debug_secret", secret)
    from app.main import create_app

    return create_app()


def test_debug_requires_secret(monkeypatch):
    app = _create_app(monkeypatch, "")
    with TestClient(app) as client:
        resp = client.get("/debug/db")
        assert resp.status_code == 404


def test_debug_wrong_secret(monkeypatch):
    app = _create_app(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.get("/debug/db")
        assert resp.status_code == 404
        resp = client.get("/debug/db", headers={"X-Debug-Secret": "x"})
        assert resp.status_code == 404


def test_debug_db_ok(monkeypatch):
    app = _create_app(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.get("/debug/db", headers={"X-Debug-Secret": "s"})
        assert resp.status_code == 200
        assert resp.json()["db"] == "ok"

