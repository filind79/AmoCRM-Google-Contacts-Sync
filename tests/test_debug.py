from datetime import datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from app.config import settings
from app.storage import Token, get_session, init_db, save_token
from app.webhooks import clear_recent_webhook_events


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


def _clear_recent_webhook_events() -> None:
    clear_recent_webhook_events()


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


def test_debug_db_query_token(monkeypatch):
    app = _create_app(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.get("/debug/db?token=s")
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

        async def post(self, url, headers=None, json=None):  # noqa: ANN001, D401
            assert url.endswith("/people:createContact")
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"resourceName": "people/test"})

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
    assert payload["can_read_connections"] is True
    assert payload["can_write_contact"] is True
    assert payload["scopes"]
    assert payload["token_expires_at"] is not None
    assert "error_reason" not in payload


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
    assert payload["can_read_connections"] is False
    assert payload["can_write_contact"] is False
    assert payload.get("scopes") == []
    assert payload.get("token_expires_at") is None
    assert payload.get("error_reason") == "token_missing"


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

        async def post(self, url, headers=None, json=None):  # noqa: ANN001, D401
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={})

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
    assert payload["can_read_connections"] is False
    assert payload["can_write_contact"] is False


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

        async def post(self, url, headers=None, json=None):  # noqa: ANN001, D401
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={})

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
    assert payload["can_read_connections"] is False
    assert payload["can_write_contact"] is False


def test_debug_webhook_last_events(monkeypatch):
    _clear_recent_webhook_events()
    monkeypatch.setattr(settings, "webhook_secret", "wh-secret")
    app = _create_app(monkeypatch, "dbg-secret")

    recorded: list[int] = []

    def fake_enqueue(contact_id: int) -> None:  # noqa: D401
        recorded.append(contact_id)

    class DummyWorker:
        def wake(self) -> None:  # noqa: D401
            return None

    monkeypatch.setattr("app.webhooks.enqueue_contact", fake_enqueue)
    monkeypatch.setattr("app.webhooks.pending_sync_worker", DummyWorker())

    with TestClient(app) as client:
        resp = client.get("/debug/webhook", headers={"X-Debug-Secret": "dbg-secret"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["accepted_auth"] == ["X-Webhook-Secret", "X-Debug-Secret", "?token"]
        assert payload["last_events"] == []

        for contact_id in range(1, 10):
            resp = client.post(
                "/webhook/amo",
                json={"event": "contact_updated", "contact_id": contact_id},
                headers={"X-Webhook-Secret": "wh-secret"},
            )
            assert resp.status_code == 200

        resp = client.post(
            "/webhook/amo",
            json={"contacts": {"update": [{"id": 99}]}},
            headers={"X-Webhook-Secret": "wh-secret"},
        )
        assert resp.status_code == 200

        for contact_id in (10, 11):
            resp = client.post(
                "/webhook/amo",
                json={"event": "contact_updated", "contact_id": contact_id},
                headers={"X-Webhook-Secret": "wh-secret"},
            )
            assert resp.status_code == 200

        resp = client.get("/debug/webhook", headers={"X-Debug-Secret": "dbg-secret"})

    assert recorded[-2:] == [10, 11]

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted_auth"] == ["X-Webhook-Secret", "X-Debug-Secret", "?token"]
    events = data["last_events"]
    assert len(events) == 10
    assert [item["contact_id"] for item in events] == [11, 10, 99, 9, 8, 7, 6, 5, 4, 3]
    assert events[0]["event"] == "contact_updated"
    assert events[1]["event"] == "contact_updated"
    assert events[2]["event"] == "contacts.update"
    for entry in events[:3]:
        assert datetime.fromisoformat(entry["ts"])


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

        async def post(self, url, headers=None, json=None):  # noqa: ANN001, D401
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: RegressionClient())

    app = _create_app(monkeypatch, "secret")
    with TestClient(app) as client:
        resp = client.get("/debug/ping-google", headers={"X-Debug-Secret": "secret"})

    payload = resp.json()
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == 200
    assert payload["scopes_ok"] is True
    assert payload["can_read_connections"] is True
    assert payload["can_write_contact"] is True

