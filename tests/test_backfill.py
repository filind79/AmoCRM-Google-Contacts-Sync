import pytest

from app.backfill import sync_contact


@pytest.mark.asyncio
async def test_sync_contact_closes_session(monkeypatch):
    class DummySession:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    dummy_session = DummySession()

    async def fake_get_contact(contact_id):  # noqa: ARG001
        return {"name": "John", "custom_fields_values": []}

    async def fake_upsert(contact_id, data):  # noqa: ARG001
        return {"resourceName": "people/1"}

    def fake_save_link(session, cid, rn):  # noqa: ARG001
        return None

    monkeypatch.setattr("app.backfill.get_contact", fake_get_contact)
    monkeypatch.setattr("app.backfill.upsert_contact_by_external_id", fake_upsert)
    monkeypatch.setattr("app.backfill.save_link", fake_save_link)
    monkeypatch.setattr("app.backfill.get_session", lambda: dummy_session)

    payload = {"amo_contact_id": 1}
    result = await sync_contact(payload)

    assert result["google_resource_name"] == "people/1"
    assert dummy_session.closed
