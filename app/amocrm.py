from typing import Any, Dict, List

import httpx

from app.config import settings
from app.storage import get_session, get_token


AMO_HEADERS = {"Content-Type": "application/json"}


async def get_access_token() -> str:
    session = get_session()
    token = get_token(session, "amocrm")
    if not token:
        raise RuntimeError("AmoCRM token missing")
    # TODO: refresh token when expired
    return token.access_token


async def get_contact(contact_id: int) -> Dict[str, Any]:
    token = await get_access_token()
    url = f"{settings.amo_base_url}/api/v4/contacts/{contact_id}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.json()


def extract_name_and_fields(contact: Dict[str, Any]) -> Dict[str, Any]:
    name = contact.get("name") or ""
    custom_fields = contact.get("custom_fields_values", [])
    phones: List[str] = []
    emails: List[str] = []
    for field in custom_fields:
        code = field.get("field_code")
        values = [v.get("value") for v in field.get("values", [])]
        if code == "PHONE":
            phones.extend(values)
        elif code == "EMAIL":
            emails.extend(values)
    return {"name": name, "phones": phones, "emails": emails}
