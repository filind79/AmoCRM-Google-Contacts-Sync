from datetime import datetime, timedelta, timezone

import asyncio
import httpx

from app import google_people
from app.google_people import _parse_rfc3339, _parse_update_time


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


def test_create_contact_external_id(monkeypatch):
    captured: dict = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):  # noqa: D401, ANN001, ARG002
            pass

        async def __aenter__(self):  # noqa: D401
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: D401, ANN001, ARG002
            return False

        async def post(self, url, headers=None, json=None):  # noqa: D401, ANN001
            captured["json"] = json
            class Resp:
                status_code = 200

                def raise_for_status(self):  # noqa: D401
                    return None

                def json(self):  # noqa: D401
                    return {}

            return Resp()

    async def fake_headers(session):  # noqa: ARG001
        return {}

    class DummySession:
        def close(self):
            pass

    monkeypatch.setattr(google_people, "_token_headers", fake_headers)
    monkeypatch.setattr(google_people, "get_session", lambda: DummySession())
    monkeypatch.setattr(httpx, "AsyncClient", DummyClient)

    asyncio.run(
        google_people.create_contact(
            {
                "name": "A",
                "emails": ["a@example.com"],
                "phones": ["+1"],
                "external_id": 123,
            }
        )
    )

    assert captured["json"]["externalIds"] == [
        {"value": "123", "type": "AMOCRM"}
    ]
