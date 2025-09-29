from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import logging

from app.config import settings
from app.integrations import google_client
from app.services.match import MatchCandidate, MatchKeys, build_candidate_from_person
from app.services.transform import union_fields
from app.storage import remap_google_links


logger = logging.getLogger(__name__)

UPDATE_PERSON_FIELDS = (
    "names,phoneNumbers,emailAddresses,memberships,biographies,externalIds"
)


class MissingEtagError(RuntimeError):
    def __init__(self, resource_name: str) -> None:
        super().__init__("Google contact missing etag")
        self.resource_name = resource_name


def _merge_external_ids(persons: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[Tuple[Optional[str], Optional[str]]] = set()
    merged: List[Dict[str, Any]] = []
    for person in persons:
        for entry in person.get("externalIds", []) or []:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            id_type = entry.get("type")
            key = (id_type, value)
            if key in seen:
                continue
            seen.add(key)
            external_entry: Dict[str, Any] = {}
            if id_type is not None:
                external_entry["type"] = id_type
            if value is not None:
                external_entry["value"] = value
            if entry.get("metadata"):
                external_entry["metadata"] = entry["metadata"]
            merged.append(external_entry)
    return merged


async def merge_contacts(
    primary: MatchCandidate,
    others: Sequence[MatchCandidate],
    *,
    keys: MatchKeys,
    group_resource_name: Optional[str] = None,
    db_session,
) -> MatchCandidate:
    duplicates = [c for c in others if c.resource_name != primary.resource_name]
    if not duplicates:
        return primary

    duplicate_names = [c.resource_name for c in duplicates]
    logger.info(
        "merge.start",
        extra={"primary": primary.resource_name, "duplicates": duplicate_names},
    )
    logger.info("merge.primary=%s", primary.resource_name)

    persons = [c.person for c in duplicates]
    resolved_group = group_resource_name
    if not resolved_group:
        group_name = (settings.google_contact_group_name or "").strip()
        if group_name:
            resolved_group = await google_client.ensure_group(group_name)

    payload = union_fields(
        primary.person,
        persons,
        ensure_group=resolved_group,
    )

    external_ids = _merge_external_ids([primary.person, *persons])
    if external_ids:
        payload["externalIds"] = external_ids

    etag = primary.person.get("etag")
    if not etag:
        raise MissingEtagError(primary.resource_name)

    updated = await google_client.update_contact(
        primary.resource_name,
        payload,
        update_person_fields=UPDATE_PERSON_FIELDS,
        etag=etag,
    )
    logger.info(
        "merge.updated",
        extra={
            "resource_name": primary.resource_name,
            "fields": sorted(payload.keys()),
        },
    )

    await google_client.batch_delete_contacts(duplicate_names)
    logger.info("merge.deleted=%s", duplicate_names)

    remap_google_links(db_session, primary.resource_name, duplicate_names)

    refreshed = build_candidate_from_person(updated, keys)
    return refreshed or primary
