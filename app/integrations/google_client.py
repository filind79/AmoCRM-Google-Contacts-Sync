from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set

from app.config import settings
from app.google_people import GOOGLE_API_BASE, _request, _token_headers
from app.storage import get_session


_GROUP_CACHE: Dict[str, str] = {}
_GROUP_LOCK = asyncio.Lock()
_GROUP_CLIENT_DATA_KEY = "amo_google_sync_group"
_WRITABLE_TOP_LEVEL_FIELDS = {
    "addresses",
    "biographies",
    "birthdays",
    "calendarUrls",
    "clientData",
    "emailAddresses",
    "events",
    "externalIds",
    "genders",
    "imClients",
    "interests",
    "locales",
    "locations",
    "memberships",
    "miscKeywords",
    "names",
    "nicknames",
    "occupations",
    "organizations",
    "phoneNumbers",
    "relations",
    "sipAddresses",
    "urls",
    "userDefined",
}


def _sanitize_entries(
    entries: Any,
    *,
    allowed_fields: Set[str],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        clean = {
            key: value
            for key, value in entry.items()
            if key in allowed_fields and value is not None
        }
        if clean:
            result.append(clean)
    return result


def sanitize_person_for_update(person: Mapping[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if not isinstance(person, Mapping):
        return payload

    for field in _WRITABLE_TOP_LEVEL_FIELDS:
        value = person.get(field)
        if value is not None:
            payload[field] = deepcopy(value)

    payload["phoneNumbers"] = _sanitize_entries(
        payload.get("phoneNumbers"),
        allowed_fields={"value", "type"},
    )
    if not payload["phoneNumbers"]:
        payload.pop("phoneNumbers", None)

    payload["emailAddresses"] = _sanitize_entries(
        payload.get("emailAddresses"),
        allowed_fields={"value", "type"},
    )
    if not payload["emailAddresses"]:
        payload.pop("emailAddresses", None)

    payload["names"] = _sanitize_entries(
        payload.get("names"),
        allowed_fields={
            "displayName",
            "givenName",
            "familyName",
            "middleName",
            "honorificPrefix",
            "honorificSuffix",
            "phoneticGivenName",
            "phoneticFamilyName",
            "phoneticMiddleName",
            "phoneticHonorificPrefix",
            "phoneticHonorificSuffix",
            "unstructuredName",
        },
    )
    if not payload["names"]:
        payload.pop("names", None)

    memberships: List[Dict[str, Any]] = []
    for entry in payload.get("memberships", []) or []:
        if not isinstance(entry, Mapping):
            continue
        group = entry.get("contactGroupMembership")
        if not isinstance(group, Mapping):
            continue
        group_name = group.get("contactGroupResourceName")
        if not group_name:
            continue
        memberships.append(
            {
                "contactGroupMembership": {
                    "contactGroupResourceName": group_name,
                }
            }
        )
    if memberships:
        payload["memberships"] = memberships
    else:
        payload.pop("memberships", None)

    payload["biographies"] = _sanitize_entries(
        payload.get("biographies"),
        allowed_fields={"value", "contentType"},
    )
    if not payload["biographies"]:
        payload.pop("biographies", None)

    payload["externalIds"] = _sanitize_entries(
        payload.get("externalIds"),
        allowed_fields={"value", "type"},
    )
    if not payload["externalIds"]:
        payload.pop("externalIds", None)

    payload["clientData"] = _sanitize_entries(
        payload.get("clientData"),
        allowed_fields={"key", "value"},
    )
    if not payload["clientData"]:
        payload.pop("clientData", None)

    return payload


def _normalize_update_fields(update_person_fields: Sequence[str] | str) -> Set[str]:
    if isinstance(update_person_fields, str):
        parts = (part.strip() for part in update_person_fields.split(","))
        return {part for part in parts if part}
    return {field for field in update_person_fields if field}


def _format_update_fields(update_fields: Sequence[str] | str) -> str:
    if isinstance(update_fields, str):
        return update_fields
    unique_fields = {field for field in update_fields if field}
    return ",".join(sorted(unique_fields))


def _format_group_memberships(
    existing: Optional[Sequence[Mapping[str, Any]]],
    *,
    resource_name: str,
) -> List[Dict[str, Any]]:
    memberships: List[Dict[str, Any]] = []
    found = False
    for membership in existing or []:
        if not isinstance(membership, Mapping):
            continue
        record = deepcopy(dict(membership))
        data = record.get("contactGroupMembership")
        if isinstance(data, Mapping):
            group_name = data.get("contactGroupResourceName")
            if group_name == resource_name:
                found = True
        memberships.append(record)
    if not found:
        memberships.append(
            {
                "contactGroupMembership": {
                    "contactGroupResourceName": resource_name,
                }
            }
        )
    return memberships


async def ensure_group(name: str) -> Optional[str]:
    if not name:
        return None

    cached = _GROUP_CACHE.get(name)
    if cached:
        return cached

    async with _GROUP_LOCK:
        cached = _GROUP_CACHE.get(name)
        if cached:
            return cached

        session = get_session()
        try:
            headers = await _token_headers(session)
        finally:
            session.close()

        url = f"{GOOGLE_API_BASE}/contactGroups"
        params: Dict[str, Any] = {"pageSize": 200, "groupFields": "name,clientData,metadata"}
        page_token: Optional[str] = None

        def _matches(group: Mapping[str, Any]) -> bool:
            if not isinstance(group, Mapping):
                return False
            metadata = group.get("metadata")
            if isinstance(metadata, Mapping) and metadata.get("deleted"):
                return False
            group_name = group.get("name")
            formatted_name = group.get("formattedName")
            if name in {group_name, formatted_name}:
                return True
            for entry in group.get("clientData", []) or []:
                if not isinstance(entry, Mapping):
                    continue
                if entry.get("key") != _GROUP_CLIENT_DATA_KEY:
                    continue
                if entry.get("value") == name:
                    return True
            return False

        while True:
            query: Dict[str, Any] = dict(params)
            if page_token:
                query["pageToken"] = page_token
            response = await _request("GET", url, headers=headers, params=query)
            payload = response.json()
            groups = payload.get("contactGroups", []) or []
            for group in groups:
                if _matches(group):
                    resource = group.get("resourceName") if isinstance(group, Mapping) else None
                    if resource:
                        _GROUP_CACHE[name] = resource
                        return resource
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        create_headers = dict(headers)
        create_headers["Content-Type"] = "application/json"
        body = {
            "contactGroup": {
                "name": name,
                "clientData": [
                    {
                        "key": _GROUP_CLIENT_DATA_KEY,
                        "value": name,
                    }
                ],
            }
        }
        response = await _request("POST", url, headers=create_headers, json=body)
        data = response.json()
        resource_name = data.get("resourceName")
        if resource_name:
            _GROUP_CACHE[name] = resource_name
        return resource_name


async def search_contacts(
    query: str,
    *,
    read_mask: str = "names,emailAddresses,phoneNumbers,metadata",
    sources: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Search contacts using ``people.searchContacts`` and return raw records."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        params: Dict[str, Any] = {"query": query, "readMask": read_mask}
        if sources:
            params["sources"] = ",".join(sources)
        url = f"{GOOGLE_API_BASE}/people:searchContacts"
        response = await _request("GET", url, params=params, headers=headers)
        return [item.get("person", {}) for item in response.json().get("results", [])]
    finally:
        session.close()


async def search_other_contacts(
    query: str,
    *,
    read_mask: str = "names,emailAddresses,phoneNumbers,metadata",
) -> List[Dict[str, Any]]:
    """Search ``Other Contacts`` using ``otherContacts.search``."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        headers["Content-Type"] = "application/json"
        body = {"query": query, "readMask": read_mask}
        url = f"{GOOGLE_API_BASE}/otherContacts:search"
        response = await _request("POST", url, headers=headers, json=body)
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
        payload: MutableMapping[str, Any] = sanitize_person_for_update(body)
        payload["resourceName"] = resource_name
        if etag:
            payload["etag"] = etag
        fields = _normalize_update_fields(update_person_fields)
        group_name = settings.google_contact_group_name.strip()
        if group_name:
            group_resource = await ensure_group(group_name)
            if group_resource:
                payload["memberships"] = _format_group_memberships(
                    payload.get("memberships"),
                    resource_name=group_resource,
                )
                fields.add("memberships")
        params = {"updatePersonFields": _format_update_fields(sorted(fields))}
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
        fields = _normalize_update_fields(update_person_fields)
        group_name = settings.google_contact_group_name.strip()
        group_resource = await ensure_group(group_name) if group_name else None
        if group_resource:
            fields.add("memberships")
        payload_contacts: Dict[str, Dict[str, Any]] = {}
        for key, data in contacts.items():
            entry = sanitize_person_for_update(data)
            if group_resource:
                entry["memberships"] = _format_group_memberships(
                    entry.get("memberships"),
                    resource_name=group_resource,
                )
            payload_contacts[key] = entry
        body = {
            "contacts": payload_contacts,
            "updateMask": _format_update_fields(sorted(fields)),
        }
        url = f"{GOOGLE_API_BASE}/people:batchUpdateContacts"
        response = await _request("POST", url, headers=headers, json=body)
        return response.json()
    finally:
        session.close()
