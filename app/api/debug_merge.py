from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.debug import require_debug_secret
from app.integrations import google_client
from app.services.match import MatchKeys, normalize_phone
from app.services.sync_apply import GoogleApplyService
from app.storage import get_link


router = APIRouter()


@router.post("/by-phone")
async def merge_by_phone(
    phone: str = Query(..., description="Phone number used for matching"),
    _=Depends(require_debug_secret),
) -> Dict[str, Any]:
    normalized = normalize_phone(phone)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid phone")
    service = GoogleApplyService()
    try:
        keys = MatchKeys(phones={normalized}, emails=set())
        result = await service.merge_candidates(keys)
        result["phone"] = normalized
        return result
    finally:
        service.close()


@router.post("/by-amo")
async def merge_by_amo(
    id: int = Query(..., description="AmoCRM contact identifier"),
    _=Depends(require_debug_secret),
) -> Dict[str, Any]:
    service = GoogleApplyService()
    try:
        link = get_link(service.db_session, str(id))
        if not link:
            raise HTTPException(status_code=404, detail="Mapping not found")
        try:
            person = await google_client.get_contact(link.google_resource_name, person_fields="names,phoneNumbers,emailAddresses,memberships,biographies,metadata")
        except Exception as exc:  # pragma: no cover - upstream errors
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        phones: List[str] = []
        for entry in person.get("phoneNumbers", []) or []:
            if isinstance(entry, dict) and entry.get("value"):
                phones.append(entry["value"])
        emails: List[str] = []
        for entry in person.get("emailAddresses", []) or []:
            if isinstance(entry, dict) and entry.get("value"):
                emails.append(entry["value"])
        keys = MatchKeys.from_raw(phones, emails)
        result = await service.merge_candidates(
            keys,
            amo_contact_id=id,
            mapped_resource=link.google_resource_name,
        )
        result["amo_id"] = id
        result["resource"] = link.google_resource_name
        return result
    finally:
        service.close()
