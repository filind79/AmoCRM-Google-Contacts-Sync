import pytest

from app.integrations import google_client


class DummyResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self):  # noqa: D401
        return self._data


class DummySession:
    def close(self):  # noqa: D401
        return None


@pytest.fixture(autouse=True)
def reset_group_cache():
    google_client._GROUP_CACHE.clear()
    yield
    google_client._GROUP_CACHE.clear()


@pytest.mark.asyncio
async def test_ensure_group_returns_existing(monkeypatch):
    calls = []

    async def fake_request(method, url, **kwargs):  # noqa: ANN001
        calls.append((method, url, kwargs))
        assert method == "GET"
        return DummyResponse(
            {
                "contactGroups": [
                    {
                        "resourceName": "contactGroups/1",
                        "name": "Target",
                    }
                ]
            }
        )

    async def fake_headers(_session):  # noqa: ANN001
        return {}

    monkeypatch.setattr(google_client, "_request", fake_request)
    monkeypatch.setattr(google_client, "_token_headers", fake_headers)
    monkeypatch.setattr(google_client, "get_session", lambda: DummySession())

    resource = await google_client.ensure_group("Target")

    assert resource == "contactGroups/1"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ensure_group_creates_when_missing(monkeypatch):
    calls = []

    async def fake_request(method, url, **kwargs):  # noqa: ANN001
        calls.append((method, url, kwargs))
        if method == "GET":
            return DummyResponse({"contactGroups": []})
        assert method == "POST"
        body = kwargs.get("json")
        assert body["contactGroup"]["name"] == "Target"
        return DummyResponse({"resourceName": "contactGroups/2"})

    async def fake_headers(_session):  # noqa: ANN001
        return {}

    monkeypatch.setattr(google_client, "_request", fake_request)
    monkeypatch.setattr(google_client, "_token_headers", fake_headers)
    monkeypatch.setattr(google_client, "get_session", lambda: DummySession())

    resource = await google_client.ensure_group("Target")

    assert resource == "contactGroups/2"
    methods = [call[0] for call in calls]
    assert methods == ["GET", "POST"]


@pytest.mark.asyncio
async def test_update_contact_injects_membership(monkeypatch):
    captured = {}

    async def fake_ensure_group(name):  # noqa: ANN001
        assert name == "Target"
        return "contactGroups/42"

    async def fake_headers(_session):  # noqa: ANN001
        return {}

    async def fake_request(method, url, **kwargs):  # noqa: ANN001
        assert method == "PATCH"
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["json"] = kwargs.get("json")
        return DummyResponse({"resourceName": "people/1"})

    monkeypatch.setattr(google_client.settings, "google_contact_group_name", "Target")
    monkeypatch.setattr(google_client, "ensure_group", fake_ensure_group)
    monkeypatch.setattr(google_client, "_token_headers", fake_headers)
    monkeypatch.setattr(google_client, "_request", fake_request)
    monkeypatch.setattr(google_client, "get_session", lambda: DummySession())

    await google_client.update_contact(
        "people/1",
        {"names": [{"displayName": "Name"}]},
        update_person_fields="names",
        etag="etag-1",
    )

    memberships = captured["json"]["memberships"]
    assert memberships[0]["contactGroupMembership"]["contactGroupResourceName"] == "contactGroups/42"
    update_mask = captured["params"]["updatePersonFields"].split(",")
    assert "memberships" in update_mask


@pytest.mark.asyncio
async def test_batch_update_contact_injects_membership(monkeypatch):
    captured = {}

    async def fake_ensure_group(name):  # noqa: ANN001
        assert name == "Target"
        return "contactGroups/42"

    async def fake_headers(_session):  # noqa: ANN001
        return {}

    async def fake_request(method, url, **kwargs):  # noqa: ANN001
        assert method == "POST"
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return DummyResponse({"updated": {}})

    monkeypatch.setattr(google_client.settings, "google_contact_group_name", "Target")
    monkeypatch.setattr(google_client, "ensure_group", fake_ensure_group)
    monkeypatch.setattr(google_client, "_token_headers", fake_headers)
    monkeypatch.setattr(google_client, "_request", fake_request)
    monkeypatch.setattr(google_client, "get_session", lambda: DummySession())

    await google_client.batch_update_contacts(
        {"people/1": {"etag": "e1"}},
        update_person_fields=["names"],
    )

    payload = captured["json"]
    contact = payload["contacts"]["people/1"]
    memberships = contact["memberships"]
    assert memberships[0]["contactGroupMembership"]["contactGroupResourceName"] == "contactGroups/42"
    update_mask = payload["updateMask"].split(",")
    assert "memberships" in update_mask
