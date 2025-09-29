from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import logging

from app.config import settings
from app.google_people import create_contact
from app.integrations import google_client
from app.services.match import (
    MatchCandidate,
    MatchContext,
    MatchKeys,
    build_candidate_from_person,
    choose_primary,
    normalize_email,
    normalize_phone,
    search_google_candidates,
)
from app.services.transform import union_fields
from app.storage import get_link, remap_google_links, save_link
from app.storage import get_session as get_db_session
from app.utils import parse_display_name


logger = logging.getLogger(__name__)

PERSON_FIELDS = "names,phoneNumbers,emailAddresses,memberships,biographies,metadata"


@dataclass(slots=True)
class ProcessResult:
    action: str
    resource_name: Optional[str]
    reason: Optional[List[str]] = None


class MissingEtagError(RuntimeError):
    def __init__(self, resource_name: str) -> None:
        super().__init__("Google contact missing etag")
        self.resource_name = resource_name


class GoogleApplyService:
    def __init__(self) -> None:
        self.group_resource_name = settings.google_contact_group_name or None
        self.auto_merge = settings.auto_merge_duplicates
        self.db_session = get_db_session()

    def close(self) -> None:
        self.db_session.close()

    async def process_contact(self, contact: Dict[str, Any]) -> ProcessResult:
        amo_id = contact.get("id")
        raw_emails = contact.get("emails", []) or []
        raw_phones = contact.get("phones", []) or []
        keys = MatchKeys.from_raw(raw_phones, raw_emails)

        link = get_link(self.db_session, str(amo_id)) if amo_id is not None else None
        mapped_resource = link.google_resource_name if link else None

        candidates = await self._find_candidates(keys, mapped_resource)
        context = MatchContext(
            amo_contact_id=amo_id,
            group_resource_name=self.group_resource_name,
            mapped_resource_name=mapped_resource,
        )
        primary = choose_primary(candidates, keys, context) if candidates else None

        duplicates: List[MatchCandidate] = []
        if primary:
            duplicates = [c for c in candidates if c.resource_name != primary.resource_name]
            if duplicates and self.auto_merge:
                primary = await self._merge_duplicates(primary, duplicates, keys)
                duplicates = []
        elif not candidates:
            # Preflight before creation
            preflight = await self._find_candidates(keys, mapped_resource)
            if preflight:
                logger.info(
                    "apply.preflight_switched_to_update",
                    extra={
                        "amo_contact_id": amo_id,
                        "resource_name": [c.resource_name for c in preflight],
                    },
                )
                primary = choose_primary(preflight, keys, context)
                candidates = preflight
                duplicates = [
                    c for c in preflight if primary and c.resource_name != primary.resource_name
                ]
                if primary and duplicates and self.auto_merge:
                    primary = await self._merge_duplicates(primary, duplicates, keys)
                    duplicates = []

        if primary:
            result = await self._update_contact(primary, contact, keys)
            if amo_id is not None:
                save_link(self.db_session, str(amo_id), result.resource_name or primary.resource_name)
            return result

        # No match -> create
        result = await self._create_contact(contact, keys)
        if amo_id is not None and result.resource_name:
            save_link(self.db_session, str(amo_id), result.resource_name)
        return result

    async def _find_candidates(
        self, keys: MatchKeys, mapped_resource: Optional[str]
    ) -> List[MatchCandidate]:
        candidates = await search_google_candidates(keys)
        resource_map = {c.resource_name: c for c in candidates}
        if mapped_resource and mapped_resource not in resource_map:
            try:
                person = await google_client.get_contact(mapped_resource, person_fields=PERSON_FIELDS)
            except Exception:  # pragma: no cover - defensive against stale mapping
                logger.warning(
                    "match.mapping_not_found",
                    extra={"resource_name": mapped_resource},
                )
            else:
                candidate = build_candidate_from_person(person, keys)
                if candidate is not None:
                    resource_map[candidate.resource_name] = candidate
        return list(resource_map.values())

    async def merge_candidates(
        self,
        keys: MatchKeys,
        *,
        amo_contact_id: Optional[int] = None,
        mapped_resource: Optional[str] = None,
    ) -> Dict[str, Any]:
        candidates = await self._find_candidates(keys, mapped_resource)
        context = MatchContext(
            amo_contact_id=amo_contact_id,
            group_resource_name=self.group_resource_name,
            mapped_resource_name=mapped_resource,
        )
        if not candidates:
            return {"merged": 0, "reason": "no_candidates"}
        primary = choose_primary(candidates, keys, context)
        if not primary:
            return {"merged": 0, "reason": "no_primary"}
        duplicates = [c for c in candidates if c.resource_name != primary.resource_name]
        if len(duplicates) < 1:
            return {
                "merged": 0,
                "reason": "single_candidate",
                "primary": primary.resource_name,
                "candidates": [c.resource_name for c in candidates],
            }
        merged_primary = await self._merge_duplicates(primary, duplicates, keys)
        if amo_contact_id is not None:
            save_link(self.db_session, str(amo_contact_id), merged_primary.resource_name)
        return {
            "merged": len(duplicates),
            "primary": merged_primary.resource_name,
            "deleted": [c.resource_name for c in duplicates],
        }

    async def _merge_duplicates(
        self,
        primary: MatchCandidate,
        duplicates: Sequence[MatchCandidate],
        keys: MatchKeys,
    ) -> MatchCandidate:
        limited = list(duplicates)[:5]
        duplicate_names = [c.resource_name for c in limited]
        logger.info(
            "merge.start",
            extra={
                "primary": primary.resource_name,
                "duplicates": duplicate_names,
            },
        )
        logger.info(
            "merge.primary",
            extra={"resource_name": primary.resource_name},
        )
        payload = union_fields(
            primary.person,
            [c.person for c in limited],
            ensure_group=self.group_resource_name,
        )
        update_fields = set(payload.keys())
        etag = primary.person.get("etag")
        if not etag:
            raise MissingEtagError(primary.resource_name)
        updated = await google_client.update_contact(
            primary.resource_name,
            payload,
            update_person_fields=sorted(update_fields),
            etag=etag,
        )
        logger.info(
            "merge.updated",
            extra={"resource_name": primary.resource_name, "fields": sorted(update_fields)},
        )
        await google_client.batch_delete_contacts(duplicate_names)
        logger.info(
            "merge.deleted",
            extra={"resource_names": duplicate_names},
        )
        remap_google_links(self.db_session, primary.resource_name, duplicate_names)
        refreshed = build_candidate_from_person(updated, keys)
        return refreshed or primary

    async def _update_contact(
        self, primary: MatchCandidate, contact: Dict[str, Any], keys: MatchKeys
    ) -> ProcessResult:
        existing_emails = {
            normalize_email(e.get("value"))
            for e in primary.person.get("emailAddresses", []) or []
            if isinstance(e, dict) and e.get("value")
        }
        existing_phones: set[str] = set()
        for entry in primary.person.get("phoneNumbers", []) or []:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            if not value:
                continue
            normalized_value = normalize_phone(value)
            if normalized_value:
                existing_phones.add(normalized_value)
        new_emails = keys.emails
        new_phones = keys.phones
        need_emails = bool(new_emails - existing_emails)
        need_phones = bool(new_phones - existing_phones)
        current_name = ""
        if primary.person.get("names"):
            current_name = primary.person["names"][0].get("displayName") or ""
        desired_name = (contact.get("name") or "").strip()
        need_name = bool(desired_name and desired_name != current_name)
        need_group = bool(
            self.group_resource_name and not primary.in_group(self.group_resource_name)
        )
        if not any([need_emails, need_phones, need_name, need_group]):
            reason: List[str] = []
            if not need_name:
                reason.append("name")
            if not need_phones:
                reason.append("phones")
            if not need_emails:
                reason.append("emails")
            if self.group_resource_name and not need_group:
                reason.append("group")
            return ProcessResult("skipped", primary.resource_name, reason=reason)

        amo_person: Dict[str, Any] = {}
        if new_phones:
            amo_person["phoneNumbers"] = [{"value": phone} for phone in sorted(new_phones)]
        if new_emails:
            amo_person["emailAddresses"] = [
                {"value": email} for email in sorted(new_emails)
            ]
        persons = [amo_person] if amo_person else []
        payload = union_fields(
            primary.person,
            persons,
            ensure_group=self.group_resource_name,
        )
        update_fields = set(payload.keys())
        if need_name:
            display_name, given_name, family_name = parse_display_name(desired_name)
            if display_name:
                name_entry: Dict[str, Any] = {
                    "metadata": {"primary": True},
                    "displayName": display_name,
                    "unstructuredName": display_name,
                }
                if given_name:
                    name_entry["givenName"] = given_name
                if family_name:
                    name_entry["familyName"] = family_name
                payload["names"] = [name_entry]
                update_fields.add("names")
        else:
            payload.pop("names", None)

        payload["externalIds"] = [
            {"value": str(contact.get("id")), "type": "AMOCRM"}
        ]
        update_fields.add("externalIds")

        etag = primary.person.get("etag")
        if not etag:
            raise MissingEtagError(primary.resource_name)
        await google_client.update_contact(
            primary.resource_name,
            payload,
            update_person_fields=sorted(update_fields),
            etag=etag,
        )
        return ProcessResult("updated", primary.resource_name)

    async def _create_contact(
        self, contact: Dict[str, Any], keys: MatchKeys
    ) -> ProcessResult:
        payload: Dict[str, Any] = {
            "name": contact.get("name"),
            "phones": sorted(keys.phones),
            "emails": sorted(keys.emails),
            "external_id": contact.get("id"),
        }
        if self.group_resource_name:
            payload["memberships"] = [
                {
                    "contactGroupMembership": {
                        "contactGroupResourceName": self.group_resource_name
                    }
                }
            ]
        created = await create_contact(payload)
        resource_name = created.get("resourceName")
        return ProcessResult("created", resource_name)


async def apply_contact(contact: Dict[str, Any]) -> ProcessResult:
    service = GoogleApplyService()
    try:
        return await service.process_contact(contact)
    finally:
        service.close()
