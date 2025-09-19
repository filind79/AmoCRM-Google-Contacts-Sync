import hashlib
import hmac
from typing import Any, Dict, List

from fastapi import APIRouter, Header, HTTPException, Request

from app.amocrm import extract_name_and_fields, get_contact
from app.google_people import upsert_contact_by_external_id
from app.storage import get_session, save_link
from app.config import settings

router = APIRouter()
processed_events: set[str] = set()


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not settings.webhook_shared_secret:
        return True
    if not signature:
        return False
    mac = hmac.new(settings.webhook_shared_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, signature)


@router.post("/webhooks/amocrm")
async def handle_webhook(
    payload: Dict[str, Any], request: Request, x_signature: str | None = Header(None)
):
    body_bytes = await request.body()
    if not verify_signature(body_bytes, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    contact_ids: List[int] = []
    events = payload.get("contacts", {}).get("update", [])
    contact_ids.extend([int(c.get("id")) for c in events if c.get("id")])

    event_id = payload.get("event_id")
    if event_id in processed_events:
        return {"status": "duplicate"}
    processed_events.add(event_id)

    session = get_session()
    try:
        results = []
        for cid in contact_ids:
            contact_data = await get_contact(cid)
            extracted = extract_name_and_fields(contact_data)
            google_contact = await upsert_contact_by_external_id(cid, extracted)
            resource_name = google_contact.get("resourceName")
            if resource_name:
                save_link(session, str(cid), resource_name)
            results.append({"amo_contact_id": cid, "google_resource_name": resource_name})
        return {"synced": results}
    finally:
        session.close()
