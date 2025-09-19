import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import app
from app import webhooks
from app.config import settings


def test_webhook(monkeypatch):
    webhooks.processed_events.clear()

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


def test_webhook_valid_signature(monkeypatch):
    webhooks.processed_events.clear()

    async def fake_get_contact(cid):
        return {"name": "John", "custom_fields_values": []}

    async def fake_upsert(amo_contact_id, data):
        return {"resourceName": f"people/{amo_contact_id}"}

    def fake_save_link(session, cid, rn):
        return None

    monkeypatch.setattr("app.webhooks.get_contact", fake_get_contact)
    monkeypatch.setattr("app.webhooks.upsert_contact_by_external_id", fake_upsert)
    monkeypatch.setattr("app.webhooks.save_link", fake_save_link)

    secret = "supersecret"
    monkeypatch.setattr(settings, "webhook_shared_secret", secret)

    client = TestClient(app)
    payload = {"event_id": "2", "contacts": {"update": [{"id": 2}]}}
    payload_bytes = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    resp = client.post(
        "/webhooks/amocrm",
        data=payload_bytes,
        headers={"Content-Type": "application/json", "X-Signature": signature},
    )
    assert resp.status_code == 200
    assert resp.json()["synced"][0]["google_resource_name"] == "people/2"
