import asyncio
from datetime import datetime, timedelta

import httpx

from app import google_people
from app.google_auth import get_valid_google_access_token
from app.storage import Token, get_session, save_token, init_db


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


def test_token_refresh(monkeypatch):
    init_db()
    session = get_session()
    expiry = datetime.utcnow() - timedelta(seconds=10)
    save_token(session, "google", "old", "refresh", expiry, scopes="")

    def fake_post(url, data, timeout):  # noqa: ARG001
        return DummyResponse(200, {"access_token": "new", "expires_in": 3600})

    monkeypatch.setattr(httpx, "post", fake_post)

    token = asyncio.run(get_valid_google_access_token(session))
    assert token == "new"
    session.close()

    session = get_session()
    stored = session.get(Token, 1)
    assert stored.access_token == "new"
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

