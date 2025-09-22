from datetime import datetime, timedelta, timezone

import asyncio
import httpx
import random
from typing import Any

import pytest

from app.google_people import (
    RateLimitError,
    _parse_rfc3339,
    _parse_update_time,
    _request,
    create_contact,
    update_contact,
)


def test_parse_rfc3339():
    dt_z = _parse_rfc3339("2024-01-02T03:04:05Z")
    dt_offset = _parse_rfc3339("2024-01-02T03:04:05+03:00")
    assert dt_z.tzinfo is not None
    assert dt_offset.tzinfo is not None


def test_filter_by_since_days():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=1)
    recent = (cutoff + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    old = (cutoff - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    p_recent = {"metadata": {"sources": [{"updateTime": recent}]}}
    p_old = {"metadata": {"sources": [{"updateTime": old}]}}
    recent_dt = _parse_update_time(p_recent)
    old_dt = _parse_update_time(p_old)
    assert recent_dt and recent_dt >= cutoff
    assert old_dt and old_dt < cutoff


@pytest.mark.asyncio
async def test_request_retry_after(monkeypatch):
    called: list[float] = []

    async def fake_sleep(delay: float) -> None:  # noqa: ANN001
        called.append(delay)

    responses = [
        httpx.Response(429, headers={"Retry-After": "5"}, request=httpx.Request("GET", "https://x")),
        httpx.Response(200, request=httpx.Request("GET", "https://x")),
    ]

    async def fake_request(self, method, url, **kwargs):  # noqa: ANN001
        return responses.pop(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(random, "uniform", lambda a, b: 0)  # noqa: ARG005
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    resp = await _request("GET", "https://x")
    assert resp.status_code == 200
    assert called and called[0] == 5


@pytest.mark.asyncio
async def test_request_resource_exhausted(monkeypatch):
    called: list[float] = []

    async def fake_sleep(delay: float) -> None:  # noqa: ANN001
        called.append(delay)

    responses = [
        httpx.Response(
            403,
            json={"error": {"status": "RESOURCE_EXHAUSTED"}},
            request=httpx.Request("GET", "https://x"),
        ),
        httpx.Response(200, request=httpx.Request("GET", "https://x")),
    ]

    async def fake_request(self, method, url, **kwargs):  # noqa: ANN001
        return responses.pop(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(random, "uniform", lambda a, b: 0)  # noqa: ARG005
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    resp = await _request("GET", "https://x")
    assert resp.status_code == 200
    assert called and called[0] == 1


@pytest.mark.asyncio
async def test_create_contact_external_id(monkeypatch):
    class DummyAsyncClient:
        def __init__(self):
            self.payload = None

        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, D401
            return False

        async def post(self, url, headers=None, json=None):  # noqa: ANN001
            self.payload = json
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"ok": True})

    class DummySession:
        def close(self):  # noqa: D401
            return None

    dummy_client = DummyAsyncClient()

    async def fake_headers(_session):  # noqa: ANN001
        return {}

    monkeypatch.setattr("app.google_people.get_session", lambda: DummySession())
    monkeypatch.setattr("app.google_people._token_headers", fake_headers)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: dummy_client)
    async def fake_sleep(delay):  # noqa: ANN001
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    data = {
        "name": "John Doe",
        "phones": ["+7 (999) 000-11-22"],
        "emails": ["test@example.com"],
        "external_id": 123,
    }

    await create_contact(data)

    assert dummy_client.payload
    assert dummy_client.payload["externalIds"] == [{"value": "123", "type": "AMOCRM"}]


@pytest.mark.asyncio
async def test_create_contact_uses_unstructured_name(monkeypatch):
    captured: dict[str, Any] = {}

    class DummySession:
        def close(self):
            return None

    async def fake_headers(_session):
        return {}

    async def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")

        class DummyResponse:
            def json(self):
                return {}

        return DummyResponse()

    monkeypatch.setattr("app.google_people.get_session", lambda: DummySession())
    monkeypatch.setattr("app.google_people._token_headers", fake_headers)
    monkeypatch.setattr("app.google_people._request", fake_request)

    await create_contact({"name": "Имя из amoCRM"})

    assert captured["json"]["names"][0]["unstructuredName"] == "Имя из amoCRM"
    assert captured["json"]["names"][0]["metadata"]["primary"] is True


@pytest.mark.asyncio
async def test_create_contact_rate_limited(monkeypatch):
    class DummyAsyncClient:
        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, D401
            return False

        async def post(self, url, headers=None, json=None):  # noqa: ANN001
            request = httpx.Request("POST", url)
            return httpx.Response(429, request=request)

    async def fake_headers(_session):  # noqa: ANN001
        return {}
    monkeypatch.setattr("app.google_people._token_headers", fake_headers)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyAsyncClient())
    async def fake_sleep(delay):  # noqa: ANN001
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(RateLimitError):
        await create_contact({"name": "x"})


@pytest.mark.asyncio
async def test_create_contact_rate_limited_without_session_patch(monkeypatch):
    class DummyAsyncClient:
        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, D401
            return False

        async def post(self, url, headers=None, json=None):  # noqa: ANN001
            request = httpx.Request("POST", url)
            return httpx.Response(429, request=request)

    async def fake_headers(_session):  # noqa: ANN001
        return {}

    monkeypatch.setattr("app.google_people._token_headers", fake_headers)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: DummyAsyncClient())
    async def fake_sleep(delay):  # noqa: ANN001
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(RateLimitError):
        await create_contact({"name": "x"})


@pytest.mark.asyncio
async def test_update_contact_uses_unstructured_name_and_etag(monkeypatch):
    captured: dict[str, Any] = {}

    class DummySession:
        def close(self):
            return None

    async def fake_headers(_session):
        return {}

    async def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["params"] = kwargs.get("params")

        class DummyResponse:
            def json(self):
                return {}

        return DummyResponse()

    monkeypatch.setattr("app.google_people.get_session", lambda: DummySession())
    monkeypatch.setattr("app.google_people._token_headers", fake_headers)
    monkeypatch.setattr("app.google_people._request", fake_request)

    data = {
        "name": "Имя из amoCRM",
        "emails": ["test@example.com"],
        "phones": ["+70000000000"],
        "external_id": 42,
    }

    await update_contact("people/123", "etag-1", data)

    assert captured["method"] == "PATCH"
    assert captured["json"]["etag"] == "etag-1"
    assert captured["json"]["names"][0]["unstructuredName"] == "Имя из amoCRM"
    assert captured["json"]["names"][0]["metadata"]["primary"] is True
    assert (
        captured["params"]["updatePersonFields"]
        == "names,phoneNumbers,emailAddresses,externalIds"
    )


@pytest.mark.asyncio
async def test_update_contact_skips_empty_name(monkeypatch):
    captured: dict[str, Any] = {}

    class DummySession:
        def close(self):
            return None

    async def fake_headers(_session):
        return {}

    async def fake_request(method, url, **kwargs):
        captured["json"] = kwargs.get("json")

        class DummyResponse:
            def json(self):
                return {}

        return DummyResponse()

    monkeypatch.setattr("app.google_people.get_session", lambda: DummySession())
    monkeypatch.setattr("app.google_people._token_headers", fake_headers)
    monkeypatch.setattr("app.google_people._request", fake_request)

    data = {"name": "   ", "emails": ["new@example.com"], "external_id": 99}

    await update_contact("people/321", "etag-2", data)

    assert "names" not in captured["json"]
    assert captured["json"]["emailAddresses"] == [{"value": "new@example.com"}]
