from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx

from app.core.config import get_settings
from app.utils import normalize_email, normalize_phone

AMO_HEADERS = {"Content-Type": "application/json"}


async def get_access_token() -> str:
    settings = get_settings()
    if settings["amo_auth_mode"] == "llt":
        token = (os.getenv("AMO_LONG_LIVED_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("AmoCRM LLT missing")
        return token
    if settings["amo_auth_mode"] == "api_key":
        api_key = (os.getenv("AMO_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("AmoCRM API key missing")
        return api_key
    raise RuntimeError(f"Unsupported AmoCRM auth mode: {settings['amo_auth_mode']}")


async def get_contact(contact_id: int) -> Dict[str, Any]:
    token = await get_access_token()
    settings = get_settings(validate=False)
    base_url = settings["amo_base_url"].rstrip("/")
    url = f"{base_url}/api/v4/contacts/{contact_id}"
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(AMO_HEADERS)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=headers)
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
                normalized_phone = normalize_phone(value)
                if normalized_phone:
                    phones.append(normalized_phone)
            elif code == "EMAIL" and value:
                emails.append(normalize_email(value))
    return {"name": name, "phones": phones, "emails": emails}
