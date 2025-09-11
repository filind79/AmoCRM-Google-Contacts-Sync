from fastapi.testclient import TestClient

from app.main import app


def test_webhook(monkeypatch):
    async def fake_get_contact(cid):
        return {"name": "John", "custom_fields_values": []}

    async def fake_upsert(amo_contact_id, data):
        return {"resourceName": f"people/{amo_contact_id}"}

    def fake_save_link(session, cid, rn):
        return None

    monkeypatch.setattr("app.webhooks.get_contact", fake_get_contact)
    monkeypatch.setattr("app.webhooks.upsert_contact_by_external_id", fake_upsert)
    monkeypatch.setattr("app.webhooks.save_link", fake_save_link)

    client = TestClient(app)
    payload = {"event_id": "1", "contacts": {"update": [{"id": 1}]}}
    resp = client.post("/webhooks/amocrm", json=payload)
    assert resp.status_code == 200
    assert resp.json()["synced"][0]["google_resource_name"] == "people/1"
