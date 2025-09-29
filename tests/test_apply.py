from types import SimpleNamespace

from fastapi.testclient import TestClient
from app import amocrm
from app.google_auth import GoogleAuthError
from app.google_people import GoogleRateLimitError
from app.services.sync_apply import ProcessResult
from app.services.sync_engine import RecoverableSyncError


def create(monkeypatch, secret: str | None = None):
    from app.config import settings

    if secret is not None:
        monkeypatch.setattr(settings, "debug_secret", secret)
    monkeypatch.setenv("AMO_AUTH_MODE", "api_key")
    monkeypatch.setenv("AMO_API_KEY", "dummy")
    from app.main import create_app

    return create_app()


def test_apply_upserts(monkeypatch):
    from app import sync as sync_module

    async def fake_fetch_amo(limit, since_days, since_minutes=None, *, amo_ids=None, stats=None):  # noqa: ARG001
        return [
            {"id": 1, "name": "a", "emails": ["a@example.com"], "phones": []},
            {"id": 2, "name": "b", "emails": ["b@example.com"], "phones": []},
            {"id": 3, "name": "c", "emails": ["c@example.com"], "phones": []},
        ]

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch_amo)
    results = {
        1: ProcessResult("updated", "people/1"),
        2: ProcessResult("skipped", "people/2", ["name", "phones", "emails"]),
        3: ProcessResult("created", "people/3"),
    }

    instances: list[object] = []

    class DummyEngine:
        def __init__(self):
            self.calls: list[int] = []
            instances.append(self)

        async def plan(self, contact):  # noqa: ANN001
            amo_id = contact["id"]
            self.calls.append(amo_id)
            return SimpleNamespace(contact=contact)

        async def apply(self, plan):  # noqa: ANN001
            amo_id = plan.contact["id"]
            return results[amo_id]

        def close(self):  # noqa: D401
            return None

    monkeypatch.setattr(sync_module, "SyncEngine", DummyEngine)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=3&direction=to_google&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["updated"] == 1
        assert data["skip_existing"] == 1
        assert data["processed"] == 3
        skip_sample = data["samples"]["skip_existing"][0]
        assert skip_sample["reason"] == ["name", "phones", "emails"]
        assert instances and instances[0].calls == [1, 2, 3]


def test_apply_missing_etag(monkeypatch):
    from app import sync as sync_module

    async def fake_fetch_amo(limit, since_days, since_minutes=None, *, amo_ids=None, stats=None):  # noqa: ARG001
        return [{"id": 1, "name": "a", "emails": ["a@example.com"], "phones": []}]

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch_amo)
    class DummyEngine:
        def __init__(self):
            pass

        async def plan(self, contact):  # noqa: ANN001
            raise RecoverableSyncError("missing_etag")

        async def apply(self, plan):  # noqa: ANN001
            raise RecoverableSyncError("missing_etag")

        def close(self):  # noqa: D401
            return None

    monkeypatch.setattr(sync_module, "SyncEngine", DummyEngine)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=1&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 0
        assert data["errors"][0]["reason"] == "missing_etag"


def test_apply_rate_limited(monkeypatch):
    from app import sync as sync_module

    async def fake_fetch_amo(limit, since_days, since_minutes=None, *, amo_ids=None, stats=None):  # noqa: ARG001
        return [
            {"id": 1, "name": "a", "emails": ["a@example.com"], "phones": []},
            {"id": 2, "name": "b", "emails": ["b@example.com"], "phones": []},
        ]

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch_amo)

    class DummyEngine:
        def __init__(self):
            self.calls: list[int] = []

        async def plan(self, contact):  # noqa: ANN001
            amo_id = contact["id"]
            if amo_id == 1:
                self.calls.append(amo_id)
                return SimpleNamespace(contact=contact)
            raise GoogleRateLimitError(12)

        async def apply(self, plan):  # noqa: ANN001
            amo_id = plan.contact["id"]
            if amo_id == 1:
                return ProcessResult("created", "people/1")
            raise GoogleRateLimitError(12)

        def close(self):  # noqa: D401
            return None

    monkeypatch.setattr(sync_module, "SyncEngine", DummyEngine)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=5&direction=to_google&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "12"
        data = resp.json()
        assert data["status"] == "rate_limited"
        assert data["processed"] == 1
        assert data["created"] == 1
        assert data["updated"] == 0
        assert data["rate_limit"]["retry_after_seconds"] == 12
        assert data["rate_limit"]["reason"] == "google_quota"


def test_apply_forbidden_without_secret_or_confirm(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=1&direction=to_google&confirm=1",
        )
        assert resp.status_code == 403
        resp = client.post(
            "/sync/contacts/apply?limit=1&direction=to_google",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 403


def test_apply_invalid_direction(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?limit=1&direction=both&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 400


def test_apply_accepts_query_token(monkeypatch):
    from app.routes import sync as sync_routes

    async def fake_apply(limit, since_days, since_minutes=None, *, amo_ids=None):  # noqa: ARG001
        assert since_minutes == 10
        assert since_days is None
        assert amo_ids is None
        return {"ok": True}

    monkeypatch.setattr(sync_routes, "apply_contacts_to_google", fake_apply)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=2&since_minutes=10&confirm=1&token=s",
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_apply_parses_amo_ids(monkeypatch):
    from app.routes import sync as sync_routes

    captured: dict[str, object] = {}

    async def fake_apply(limit, since_days, since_minutes=None, *, amo_ids=None):  # noqa: ARG001
        captured["params"] = (limit, since_days, since_minutes, amo_ids)
        return {"status": "ok"}

    monkeypatch.setattr(sync_routes, "apply_contacts_to_google", fake_apply)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1&amo_ids=1,2,3",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert captured["params"] == (5, None, None, [1, 2, 3])


def test_apply_rejects_invalid_amo_ids(monkeypatch):
    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1&amo_ids=1,abc",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Invalid amo_ids"}


def test_apply_google_auth_error_to_401(monkeypatch):
    from app.routes import sync as sync_routes

    async def fake_apply(limit, since_days, since_minutes=None, *, amo_ids=None):  # noqa: ARG001
        raise GoogleAuthError("no_token")

    monkeypatch.setattr(sync_routes, "apply_contacts_to_google", fake_apply)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 401
        assert resp.json() == {
            "detail": "Google auth required",
            "auth_url": "/auth/google/start",
        }


def test_apply_generic_error_to_502(monkeypatch):
    from app.routes import sync as sync_routes

    async def fake_apply(limit, since_days, since_minutes=None, *, amo_ids=None):  # noqa: ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr(sync_routes, "apply_contacts_to_google", fake_apply)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 502
        assert resp.json() == {"detail": "Apply failed: boom"}


def test_apply_success_passthrough(monkeypatch):
    from app.routes import sync as sync_routes

    async def fake_apply(limit, since_days, since_minutes=None, *, amo_ids=None):  # noqa: ARG001
        return {"status": "ok", "created": 3, "skipped": 2}

    monkeypatch.setattr(sync_routes, "apply_contacts_to_google", fake_apply)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "created": 3, "skipped": 2}


def test_apply_skips_none_custom_fields_contacts_no_crash(monkeypatch):
    from app import sync as sync_module

    async def fake_fetch(limit, since_days, since_minutes=None, *, amo_ids=None, stats=None):  # noqa: ARG001
        raw = [{"id": 1, "name": "", "custom_fields_values": None}]
        parsed = []
        for c in raw:
            fields = amocrm.extract_name_and_fields(c)
            parsed.append({"id": c["id"], "name": fields["name"], "emails": fields["emails"], "phones": fields["phones"]})
        return parsed

    monkeypatch.setattr(sync_module, "fetch_amo_contacts", fake_fetch)

    class DummyEngine:
        def __init__(self):
            pass

        async def plan(self, contact):  # noqa: ANN001
            return SimpleNamespace(contact=contact)

        async def apply(self, plan):  # noqa: ANN001
            return ProcessResult("created", f"people/{plan.contact['id']}")

        def close(self):  # noqa: D401
            return None

    monkeypatch.setattr(sync_module, "SyncEngine", DummyEngine)

    app = create(monkeypatch, "s")
    with TestClient(app) as client:
        resp = client.post(
            "/sync/contacts/apply?direction=to_google&limit=5&confirm=1",
            headers={"X-Debug-Secret": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1

