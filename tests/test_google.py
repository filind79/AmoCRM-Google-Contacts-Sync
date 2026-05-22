import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from app import google_people
from app.auth import auth_google_callback
from app.google_auth import (
    GoogleAuthError,
    get_google_auth_state,
    get_valid_google_access_token,
    mark_google_auth_ready,
)
from app.storage import Setting, Token, get_session, init_db, save_token


class DummyResponse:
    def __init__(self, status_code: int, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}
        self.headers = {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)


def _reset_google_settings(session) -> None:
    session.query(Setting).filter(Setting.key.like("google_%")).delete(synchronize_session=False)
    session.commit()


def test_token_refresh(monkeypatch):
    init_db()
    session = get_session()
    _reset_google_settings(session)
    expiry = datetime.utcnow() - timedelta(seconds=10)
    save_token(session, "google", "old", "refresh", expiry, scopes="")
    mark_google_auth_ready(session)

    def fake_post(url, data, timeout):  # noqa: ARG001
        return DummyResponse(200, {"access_token": "new", "expires_in": 3600})

    monkeypatch.setattr(httpx, "post", fake_post)

    token = asyncio.run(get_valid_google_access_token(session))
    assert token == "new"
    state = get_google_auth_state(session)
    assert state.auth_status == "ok"
    session.close()

    session = get_session()
    stored = session.get(Token, 1)
    assert stored.access_token == "new"
    session.close()


def test_refresh_failure_marks_needs_reauth(monkeypatch):
    init_db()
    session = get_session()
    _reset_google_settings(session)
    expiry = datetime.utcnow() - timedelta(seconds=10)
    save_token(session, "google", "old", "refresh", expiry, scopes="")
    mark_google_auth_ready(session)

    def fake_post(url, data, timeout):  # noqa: ARG001
        return DummyResponse(400)

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(GoogleAuthError):
        asyncio.run(get_valid_google_access_token(session))

    state = get_google_auth_state(session)
    assert state.auth_status == "needs_reauth"
    assert state.failure_count == 1
    assert state.last_failure_at is not None
    session.close()


def test_refresh_failure_alert_sent_once_on_state_change(monkeypatch):
    init_db()
    session = get_session()
    _reset_google_settings(session)
    expiry = datetime.utcnow() - timedelta(seconds=10)
    save_token(session, "google", "old", "refresh", expiry, scopes="")
    mark_google_auth_ready(session)

    sent: list[str] = []

    def fake_send(message: str) -> None:
        sent.append(message)

    def fake_post(url, data, timeout):  # noqa: ARG001
        return DummyResponse(400)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("app.alerts.send_telegram_alert", fake_send)

    with pytest.raises(GoogleAuthError):
        asyncio.run(get_valid_google_access_token(session))
    with pytest.raises(GoogleAuthError):
        asyncio.run(get_valid_google_access_token(session))

    state = get_google_auth_state(session)
    assert state.auth_status == "needs_reauth"
    assert state.failure_count == 2
    assert len(sent) == 1
    session.close()


def test_refresh_success_after_failure_sends_recovery_once(monkeypatch):
    init_db()
    session = get_session()
    _reset_google_settings(session)
    expiry = datetime.utcnow() - timedelta(seconds=10)
    save_token(session, "google", "old", "refresh", expiry, scopes="")
    mark_google_auth_ready(session)

    sent: list[str] = []

    def fake_send(message: str) -> None:
        sent.append(message)

    def fail_post(url, data, timeout):  # noqa: ARG001
        return DummyResponse(400)

    def ok_post(url, data, timeout):  # noqa: ARG001
        return DummyResponse(200, {"access_token": "new", "expires_in": 3600})

    monkeypatch.setattr("app.alerts.send_telegram_alert", fake_send)
    monkeypatch.setattr(httpx, "post", fail_post)
    with pytest.raises(GoogleAuthError):
        asyncio.run(get_valid_google_access_token(session))

    monkeypatch.setattr(httpx, "post", ok_post)
    token = asyncio.run(get_valid_google_access_token(session))
    assert token == "new"

    state = get_google_auth_state(session)
    assert state.auth_status == "ok"
    assert state.failure_count == 0
    assert len(sent) == 2
    assert "Потеряна авторизация Google Contacts" in sent[0]
    assert "Восстановлено" in sent[1]
    session.close()


def test_people_client_retries(monkeypatch):
    init_db()
    session = get_session()
    expiry = datetime.utcnow() + timedelta(hours=1)
    save_token(session, "google", "t1", "r", expiry, scopes="")
    session.close()

    async def fake_get_valid(session):  # noqa: ARG001
        return "t1"

    async def fake_force_refresh(session):  # noqa: ARG001
        sess = get_session()
        save_token(sess, "google", "t2", "r", expiry, scopes="")
        sess.close()
        return "t2"

    class FakeClient:
        calls = 0

        def __init__(self, *args, **kwargs):  # noqa: D401, ANN001, ARG002
            pass

        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: D401, ANN001, ARG002
            return False

        async def request(self, method, url, params=None, headers=None, json=None):  # noqa: D401, ANN001
            assert method == "GET"
            FakeClient.calls += 1
            if FakeClient.calls == 1:
                return DummyResponse(401)
            return DummyResponse(
                200,
                {
                    "connections": [
                        {
                            "resourceName": "people/1",
                            "names": [{"displayName": "N"}],
                            "emailAddresses": [{"value": "a"}],
                        }
                    ]
                },
            )

    monkeypatch.setattr(google_people, "get_valid_google_access_token", fake_get_valid)
    monkeypatch.setattr(google_people, "force_refresh_google_access_token", fake_force_refresh)
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    contacts = asyncio.run(google_people.list_contacts(10))
    assert len(contacts) == 1


@pytest.mark.asyncio
async def test_google_callback_reuses_refresh_token(monkeypatch):
    init_db()
    base_session = get_session()
    _reset_google_settings(base_session)
    base_session.query(Token).delete()
    base_session.commit()
    save_token(base_session, "google", "old", "refresh", None, scopes="")
    base_session.close()

    real_get_session = get_session

    class TrackingSession:
        def __init__(self, inner):
            self._inner = inner
            self.closed = False

        def close(self):  # noqa: D401
            self.closed = True
            return self._inner.close()

        def __getattr__(self, item):  # noqa: D401
            return getattr(self._inner, item)

    holder = {}

    def fake_get_session():
        session = TrackingSession(real_get_session())
        holder["session"] = session
        return session

    class DummyAsyncClient:
        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, D401
            return False

        async def post(self, url, data=None):  # noqa: ANN001, D401
            assert url == "https://oauth2.googleapis.com/token"
            return DummyResponse(200, {"access_token": "new", "expires_in": 3600})

    monkeypatch.setattr("app.auth.get_session", fake_get_session)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyAsyncClient())

    await auth_google_callback("code")

    tracked_session = holder["session"]
    assert tracked_session.closed is True

    session = real_get_session()
    stored = session.query(Token).filter(Token.system == "google").one()
    assert stored.access_token == "new"
    assert stored.refresh_token == "refresh"
    state = get_google_auth_state(session)
    assert state.auth_status == "ok"
    session.close()
