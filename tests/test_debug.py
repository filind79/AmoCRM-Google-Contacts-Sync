from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.storage import init_db


def test_debug_requires_secret(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "s")
    client = TestClient(app)
    resp = client.get("/debug/db/token")
    assert resp.status_code == 401
    resp = client.get("/debug/db/token?key=wrong")
    assert resp.status_code == 401
    resp = client.get("/debug/db/token", headers={"X-Debug-Secret": "wrong"})
    assert resp.status_code == 401


def test_debug_missing_secret(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "")
    client = TestClient(app)
    resp = client.get("/debug/db/token?key=any")
    assert resp.status_code == 500


def test_debug_with_correct_key(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "s")
    init_db()
    client = TestClient(app)
    resp = client.get("/debug/db/token?key=s")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_debug_with_correct_header(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "s")
    init_db()
    client = TestClient(app)
    resp = client.get("/debug/db/token", headers={"X-Debug-Secret": "s"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
