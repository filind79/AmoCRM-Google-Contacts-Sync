"""Lightweight Google People API client used by the service.

The module exposes helper functions for listing contacts and updating/creating
them.  Authentication is delegated to :mod:`app.google_auth` which ensures that
an up to date access token is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.google_auth import (
    GoogleAuthError,
    force_refresh_google_access_token,
    get_valid_google_access_token,
)
from app.storage import get_session
from app.utils import normalize_email, normalize_phone, unique


GOOGLE_API_BASE = "https://people.googleapis.com/v1"


@dataclass
class Contact:
    resource_id: str
    name: str
    email: Optional[str]
    phone: Optional[str]


async def _token_headers(session) -> Dict[str, str]:
    token = await get_valid_google_access_token(session)
    return {"Authorization": f"Bearer {token}"}


async def list_contacts(limit: int, since_days: Optional[int] = None) -> List[Contact]:
    """Fetch a list of contacts from Google People API."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        params = {
            "personFields": "names,emailAddresses,phoneNumbers",
            "pageSize": limit,
        }
        url = f"{GOOGLE_API_BASE}/people/me/connections"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 401:
                # force refresh and retry once
                new_token = await force_refresh_google_access_token(session)
                headers["Authorization"] = f"Bearer {new_token}"
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 401:
                    raise GoogleAuthError("unauthorised", "/auth/google/start")
        resp.raise_for_status()
        data = resp.json().get("connections", [])
    finally:
        session.close()

    contacts: List[Contact] = []
    for person in data:
        names = person.get("names", [])
        name = names[0].get("displayName") if names else ""
        emails = [e.get("value") for e in person.get("emailAddresses", []) if e.get("value")]
        phones = [p.get("value") for p in person.get("phoneNumbers", []) if p.get("value")]
        contacts.append(
            Contact(
                resource_id=person.get("resourceName"),
                name=name,
                email=emails[0] if emails else None,
                phone=phones[0] if phones else None,
            )
        )
    return contacts


async def get_access_token() -> str:
    """Return a valid access token (wrapper for backward compatibility)."""

    session = get_session()
    try:
        return await get_valid_google_access_token(session)
    finally:
        session.close()


async def upsert_contact_by_external_id(amo_contact_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a Google contact identified by AmoCRM contact ID."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        headers["Content-Type"] = "application/json"
        external_id = {"value": str(amo_contact_id), "type": "AMOCRM"}
        url = f"{GOOGLE_API_BASE}/people:searchContacts"
        params = {"query": str(amo_contact_id), "readMask": "names,phoneNumbers,emailAddresses"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            resource_name = None
            if results:
                resource_name = results[0]["person"]["resourceName"]

            body = {"names": [{"displayName": data["name"]}], "externalIds": [external_id]}
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
            create_url = f"{GOOGLE_API_BASE}/people:createContact"
            resp = await client.post(create_url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()
    finally:
        session.close()

