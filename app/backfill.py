from typing import Any, Dict

from fastapi import APIRouter

from app.amocrm import get_contact, extract_name_and_fields
from app.google_people import upsert_contact_by_external_id
from app.storage import get_session, save_link

router = APIRouter()


@router.post("/backfill/sync-contact")
async def sync_contact(payload: Dict[str, Any]):
    cid = int(payload["amo_contact_id"])
    contact_data = await get_contact(cid)
    extracted = extract_name_and_fields(contact_data)
    google_contact = await upsert_contact_by_external_id(cid, extracted)
    resource_name = google_contact.get("resourceName")
    if resource_name:
        session = get_session()
        try:
            save_link(session, str(cid), resource_name)
        finally:
            session.close()
    return {"amo_contact_id": cid, "google_resource_name": resource_name}


@router.post("/backfill/sync-all")
async def sync_all(payload: Dict[str, Any] | None = None):
    # TODO: implement pagination over AmoCRM contacts
    return {"status": "not_implemented"}
