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
    """Extract basic fields from an AmoCRM contact.

    The Amo API may return ``custom_fields_values`` as ``None`` or contain
    malformed structures without ``field_code`` or ``values``.  This function
    tolerates such cases and always returns a dictionary with ``name``,
    ``phones`` and ``emails`` keys.
    """

    name = contact.get("name") or " ".join(
        filter(None, [contact.get("first_name", ""), contact.get("last_name", "")])
    )
    custom_fields = contact.get("custom_fields_values") or []
    phones: List[str] = []
    emails: List[str] = []
    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        code = field.get("field_code")
        values = field.get("values") or []
        if not code or not isinstance(values, list):
            continue
        for v in values:
            if not isinstance(v, dict):
                continue
            value = v.get("value")
            if code == "PHONE" and value:
                phones.append(value)
            elif code == "EMAIL" and value:
                emails.append(value)
    return {"name": name, "phones": phones, "emails": emails}
