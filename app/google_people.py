from typing import Any, Dict

import httpx
from app.storage import get_session, get_token
from app.utils import normalize_email, normalize_phone, unique


GOOGLE_API_BASE = "https://people.googleapis.com/v1"


async def get_access_token() -> str:
    session = get_session()
    token = get_token(session, "google")
    if not token:
        raise RuntimeError("Google token missing")
    # TODO: refresh token when expired
    return token.access_token


async def upsert_contact_by_external_id(amo_contact_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    token = await get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    external_id = {
        "value": str(amo_contact_id),
        "type": "AMOCRM",
    }
    url = f"{GOOGLE_API_BASE}/people:searchContacts"
    params = {"query": str(amo_contact_id), "readMask": "names,phoneNumbers,emailAddresses"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        resource_name = None
        if results:
            resource_name = results[0]["person"]["resourceName"]

        body = {
            "names": [{"displayName": data["name"]}],
            "externalIds": [external_id],
        }
        phones = [normalize_phone(p) for p in data.get("phones", [])]
        phones = unique(phones)
        if phones:
            body["phoneNumbers"] = [{"value": p} for p in phones]
        emails = [normalize_email(e) for e in data.get("emails", [])]
        emails = unique(emails)
        if emails:
            body["emailAddresses"] = [{"value": e} for e in emails if e]

        if resource_name:
            update_url = f"{GOOGLE_API_BASE}/{resource_name}:updateContact"
            update_params = {"updatePersonFields": "names,phoneNumbers,emailAddresses"}
            resp = await client.patch(update_url, params=update_params, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()
        else:
            create_url = f"{GOOGLE_API_BASE}/people:createContact"
            resp = await client.post(create_url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()
