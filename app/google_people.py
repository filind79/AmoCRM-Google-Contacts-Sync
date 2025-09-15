"""Lightweight Google People API client used by the service.

The module exposes helper functions for listing contacts and updating/creating
them.  Authentication is delegated to :mod:`app.google_auth` which ensures that
an up to date access token is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
    update_time: Optional[datetime] = None


async def _token_headers(session) -> Dict[str, str]:
    token = await get_valid_google_access_token(session)
    return {"Authorization": f"Bearer {token}"}


def _parse_update_time(person: Dict[str, Any]) -> Optional[datetime]:
    sources = person.get("metadata", {}).get("sources", [])
    for src in sources:
        ts = src.get("updateTime")
        if ts:
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


async def list_contacts(
    limit: int,
    since_days: Optional[int] = None,
    counters: Optional[Dict[str, int]] = None,
) -> List[Contact]:
    """Fetch a list of contacts from Google People API with pagination."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        url = f"{GOOGLE_API_BASE}/people/me/connections"
        collected: List[Contact] = []
        page_token: Optional[str] = None
        while len(collected) < limit:
            params: Dict[str, Any] = {
                "personFields": "names,emailAddresses,phoneNumbers,metadata",
                "pageSize": min(200, limit - len(collected)),
            }
            if page_token:
                params["pageToken"] = page_token
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 401:
                    new_token = await force_refresh_google_access_token(session)
                    headers["Authorization"] = f"Bearer {new_token}"
                    resp = await client.get(url, params=params, headers=headers)
                    if resp.status_code == 401:
                        raise GoogleAuthError("unauthorised", "/auth/google/start")
            if counters is not None:
                counters["requests"] = counters.get("requests", 0) + 1
            resp.raise_for_status()
            data = resp.json()
            persons = data.get("connections", [])
            if counters is not None:
                counters["considered"] = counters.get("considered", 0) + len(persons)
            for person in persons:
                upd = _parse_update_time(person)
                if since_days is not None and upd is not None:
                    if upd < datetime.utcnow() - timedelta(days=since_days):
                        continue
                names = person.get("names", [])
                name = names[0].get("displayName") if names else ""
                emails = [e.get("value") for e in person.get("emailAddresses", []) if e.get("value")]
                phones = [p.get("value") for p in person.get("phoneNumbers", []) if p.get("value")]
                collected.append(
                    Contact(
                        resource_id=person.get("resourceName"),
                        name=name,
                        email=emails[0] if emails else None,
                        phone=phones[0] if phones else None,
                        update_time=upd,
                    )
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    finally:
        session.close()

    return collected


async def search_contacts(
    query: str,
    counters: Optional[Dict[str, int]] = None,
) -> List[Contact]:
    session = get_session()
    try:
        headers = await _token_headers(session)
        params = {
            "query": query,
            "readMask": "names,emailAddresses,phoneNumbers,metadata",
        }
        url = f"{GOOGLE_API_BASE}/people:searchContacts"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=headers)
        if counters is not None:
            counters["requests"] = counters.get("requests", 0) + 1
        resp.raise_for_status()
        data = resp.json().get("results", [])
    finally:
        session.close()

    contacts: List[Contact] = []
    for item in data:
        person = item.get("person", {})
        upd = _parse_update_time(person)
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
                update_time=upd,
            )
        )
    if counters is not None:
        counters["considered"] = counters.get("considered", 0) + len(contacts)
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

            action = "update" if resource_name else "create"
            if resource_name:
                update_url = f"{GOOGLE_API_BASE}/{resource_name}:updateContact"
                update_params = {"updatePersonFields": "names,phoneNumbers,emailAddresses"}
                resp = await client.patch(update_url, params=update_params, headers=headers, json=body)
            else:
                create_url = f"{GOOGLE_API_BASE}/people:createContact"
                resp = await client.post(create_url, headers=headers, json=body)
            resp.raise_for_status()
            result = resp.json()
            result["action"] = action
            return result
    finally:
        session.close()


async def create_contact(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new contact in Google People."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        headers["Content-Type"] = "application/json"
        body: Dict[str, Any] = {"names": [{"displayName": data.get("name", "")}]}
        phones = unique([normalize_phone(p) for p in data.get("phones", []) if p])
        if phones:
            body["phoneNumbers"] = [{"value": p} for p in phones]
        emails = unique([normalize_email(e) for e in data.get("emails", []) if e])
        if emails:
            body["emailAddresses"] = [{"value": e} for e in emails]
        url = f"{GOOGLE_API_BASE}/people:createContact"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()
    finally:
        session.close()

