from typing import Any, Dict

from fastapi import APIRouter

from app.amocrm import get_contact, extract_name_and_fields
from app.services.sync_engine import SyncEngine
from app.storage import get_session, save_link

router = APIRouter()


@router.post("/backfill/sync-contact")
async def sync_contact(payload: Dict[str, Any]):
    cid = int(payload["amo_contact_id"])
    contact_data = await get_contact(cid)
    extracted = extract_name_and_fields(contact_data)
    engine = SyncEngine()
    try:
        extracted["id"] = cid
        plan = await engine.plan(extracted)
        result = await engine.apply(plan)
    finally:
        engine.close()
    resource_name = result.resource_name
    if resource_name:
        session = get_session()
        save_link(session, str(cid), resource_name)
    return {
        "amo_contact_id": cid,
        "google_resource_name": resource_name,
        "action": result.action,
    }


@router.post("/backfill/sync-all")
async def sync_all(payload: Dict[str, Any] | None = None):
    # TODO: implement pagination over AmoCRM contacts
    return {"status": "not_implemented"}
