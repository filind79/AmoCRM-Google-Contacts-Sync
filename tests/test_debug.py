from datetime import datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from app.config import settings
from app.storage import Token, get_session, init_db, save_token


def _create_app(monkeypatch, secret: str):
    from app.config import settings

    monkeypatch.setattr(settings, "debug_secret", secret)
    from app.main import create_app

    return create_app()


def _clear_tokens() -> None:
    init_db()
    session = get_session()
    try:
        session.query(Token).delete()
        session.commit()
    finally:
        session.close()


def _store_google_token(scopes: str | None = None) -> None:
    init_db()
    session = get_session()
    try:
        expiry = datetime.utcnow() + timedelta(hours=1)
        save_token(
            session,
            "google",
            access_token="access-token",
            refresh_token="refresh-token",
            expiry=expiry,
            scopes=scopes if scopes is not None else settings.google_scopes,
        )
    finally:
        session.close()


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


def test_ping_google_success(monkeypatch):
    _clear_tokens()
    _store_google_token()

    class DummyClient:
        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, D401
            return False

        async def get(self, url, headers=None, params=None):  # noqa: ANN001
            assert url.endswith("/people/me/connections")
            assert params == {"personFields": "metadata", "pageSize": 1}
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request, json={"metadata": {}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyClient())

    app = _create_app(monkeypatch, "secret")
    with TestClient(app) as client:
        resp = client.get("/debug/ping-google", headers={"X-Debug-Secret": "secret"})

    payload = resp.json()
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == 200
    assert payload["scopes_ok"] is True
    assert payload["latency_ms"] >= 1


def test_ping_google_missing_token(monkeypatch):
    _clear_tokens()
    app = _create_app(monkeypatch, "secret")
    with TestClient(app) as client:
        resp = client.get("/debug/ping-google", headers={"X-Debug-Secret": "secret"})

    payload = resp.json()
    assert resp.status_code == 200
    assert payload["ok"] is False
    assert payload["status"] in (401, 403)
    assert payload["scopes_ok"] is False
    assert "error" in payload


def test_ping_google_rate_limited(monkeypatch):
    _clear_tokens()
    _store_google_token()

    class RateLimitedClient:
        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, D401
            return False

        async def get(self, url, headers=None, params=None):  # noqa: ANN001
            assert url.endswith("/people/me/connections")
            assert params == {"personFields": "metadata", "pageSize": 1}
            request = httpx.Request("GET", url)
            return httpx.Response(
                429,
                request=request,
                headers={"Retry-After": "7"},
                json={"error": {"message": "rate limited"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: RateLimitedClient())

    app = _create_app(monkeypatch, "secret")
    with TestClient(app) as client:
        resp = client.get("/debug/ping-google", headers={"X-Debug-Secret": "secret"})

    payload = resp.json()
    assert resp.status_code == 200
    assert payload["ok"] is False
    assert payload["status"] == 429
    assert payload["retry_after"] == 7
    assert payload["scopes_ok"] is True
    assert payload["error"] == "rate limited"


def test_ping_google_forbidden(monkeypatch):
    _clear_tokens()
    _store_google_token(scopes="")

    class ForbiddenClient:
        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, D401
            return False

        async def get(self, url, headers=None, params=None):  # noqa: ANN001
            assert url.endswith("/people/me/connections")
            assert params == {"personFields": "metadata", "pageSize": 1}
            request = httpx.Request("GET", url)
            return httpx.Response(
                403,
                request=request,
                json={"error": {"message": "insufficient scopes", "status": "PERMISSION_DENIED"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: ForbiddenClient())

    app = _create_app(monkeypatch, "secret")
    with TestClient(app) as client:
        resp = client.get("/debug/ping-google", headers={"X-Debug-Secret": "secret"})

    payload = resp.json()
    assert resp.status_code == 200
    assert payload["ok"] is False
    assert payload["status"] == 403
    assert payload["scopes_ok"] is False
    assert payload["error"] == "insufficient scopes"


def test_ping_google_profile_scope_regression(monkeypatch):
    _clear_tokens()
    _store_google_token()

    class RegressionClient:
        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, D401
            return False

        async def get(self, url, headers=None, params=None):  # noqa: ANN001
            # Simulate Google rejecting the old `people/me` probe when only the contacts scope is present
            if url.endswith("/people/me"):
                request = httpx.Request("GET", url)
                return httpx.Response(
                    403,
                    request=request,
                    json={
                        "error": {
                            "message": "Request requires one of the following scopes: [profile]",
                            "status": "PERMISSION_DENIED",
                        }
                    },
                )

            assert url.endswith("/people/me/connections")
            assert params == {"personFields": "metadata", "pageSize": 1}
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request, json={"connections": []})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: RegressionClient())

    app = _create_app(monkeypatch, "secret")
    with TestClient(app) as client:
        resp = client.get("/debug/ping-google", headers={"X-Debug-Secret": "secret"})

    payload = resp.json()
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == 200
    assert payload["scopes_ok"] is True

