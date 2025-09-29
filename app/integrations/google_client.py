from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

from app.google_people import GOOGLE_API_BASE, _request, _token_headers
from app.storage import get_session

def _format_update_fields(update_fields: Sequence[str] | str) -> str:
    if isinstance(update_fields, str):
        return update_fields
    unique_fields = {field for field in update_fields if field}
    return ",".join(sorted(unique_fields))


async def search_contacts(
    query: str,
    *,
    read_mask: str = "names,emailAddresses,phoneNumbers,metadata",
) -> List[Dict[str, Any]]:
    """Search contacts using ``people.searchContacts`` and return raw records."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        params = {"query": query, "readMask": read_mask}
        url = f"{GOOGLE_API_BASE}/people:searchContacts"
        response = await _request("GET", url, params=params, headers=headers)
        return [item.get("person", {}) for item in response.json().get("results", [])]
    finally:
        session.close()


async def get_contact(resource_name: str, *, person_fields: str) -> Dict[str, Any]:
    """Retrieve a contact record via ``people.get`` with the given fields."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        params = {"personFields": person_fields}
        url = f"{GOOGLE_API_BASE}/{resource_name}"
        response = await _request("GET", url, params=params, headers=headers)
        return response.json()
    finally:
        session.close()


async def update_contact(
    resource_name: str,
    body: Mapping[str, Any],
    *,
    update_person_fields: Sequence[str] | str,
    etag: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a contact via ``people.updateContact``."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        headers["Content-Type"] = "application/json"
        payload: MutableMapping[str, Any] = dict(body)
        payload["resourceName"] = resource_name
        if etag:
            payload["etag"] = etag
        params = {"updatePersonFields": _format_update_fields(update_person_fields)}
        url = f"{GOOGLE_API_BASE}/{resource_name}:updateContact"
        response = await _request("PATCH", url, params=params, headers=headers, json=payload)
        return response.json()
    finally:
        session.close()


async def batch_delete_contacts(resource_names: Iterable[str]) -> None:
    """Delete a batch of contacts via ``people.batchDeleteContacts``."""

    names = [name for name in resource_names if name]
    if not names:
        return
    session = get_session()
    try:
        headers = await _token_headers(session)
        headers["Content-Type"] = "application/json"
        body = {"resourceNames": names}
        url = f"{GOOGLE_API_BASE}/people:batchDeleteContacts"
        await _request("POST", url, headers=headers, json=body)
    finally:
        session.close()


async def batch_update_contacts(
    contacts: Mapping[str, Mapping[str, Any]],
    *,
    update_person_fields: Sequence[str] | str,
) -> Dict[str, Any]:
    """Perform a batch update for multiple contacts."""

    if not contacts:
        return {}
    session = get_session()
    try:
        headers = await _token_headers(session)
        headers["Content-Type"] = "application/json"
        body = {
            "contacts": contacts,
            "updateMask": _format_update_fields(update_person_fields),
        }
        url = f"{GOOGLE_API_BASE}/people:batchUpdateContacts"
        response = await _request("POST", url, headers=headers, json=body)
        return response.json()
    finally:
        session.close()
