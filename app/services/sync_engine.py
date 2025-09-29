from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import logging

import httpx

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
from app.services.merge import MissingEtagError, merge_contacts
from app.services.transform import union_fields
from app.storage import get_link, remap_google_links, save_link
from app.storage import get_session as get_db_session
from app.utils import parse_display_name


logger = logging.getLogger(__name__)

PERSON_FIELDS = "names,phoneNumbers,emailAddresses,memberships,biographies,metadata"


@dataclass(slots=True)
class SyncCandidateInfo:
    resource_name: str
    in_group: bool
    has_external_id: bool
    matched_phones: List[str] = field(default_factory=list)
    matched_emails: List[str] = field(default_factory=list)


@dataclass(slots=True)
class SyncPlan:
    contact: Dict[str, Any]
    amo_contact_id: Optional[int]
    keys: MatchKeys
    action: str
    reason: str
    candidates: List[MatchCandidate] = field(default_factory=list)
    primary: Optional[MatchCandidate] = None
    duplicates: List[MatchCandidate] = field(default_factory=list)
    mapped_resource_name: Optional[str] = None
    group_resource_name: Optional[str] = None
    candidate_info: List[SyncCandidateInfo] = field(default_factory=list)
    preflight_blocked_create: bool = False


@dataclass(slots=True)
class SyncResult:
    action: str
    resource_name: Optional[str]
    reason: Optional[str] = None
    merged: List[str] = field(default_factory=list)
    primary: Optional[str] = None


class RecoverableSyncError(RuntimeError):
    """Raised when an update should be retried with a refreshed plan."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SyncEngine:
    def __init__(self) -> None:
        self.group_name = (settings.google_contact_group_name or "").strip()
        self.group_resource_name: Optional[str] = None
        self.auto_merge = settings.auto_merge_duplicates
        self.db_session = get_db_session()

    def close(self) -> None:
        self.db_session.close()

    async def _ensure_group(self) -> Optional[str]:
        if not self.group_name:
            return None
        if self.group_resource_name:
            return self.group_resource_name
        resource = await google_client.ensure_group(self.group_name)
        self.group_resource_name = resource or None
        return self.group_resource_name

    async def plan(self, contact: Dict[str, Any]) -> SyncPlan:
        amo_id = contact.get("id")
        raw_emails = contact.get("emails", []) or []
        raw_phones = contact.get("phones", []) or []
        keys = MatchKeys.from_raw(raw_phones, raw_emails)

        if not keys:
            logger.info(
                "apply.skipped_invalid_keys",
                extra={"amo_contact_id": amo_id},
            )
            return SyncPlan(
                contact=contact,
                amo_contact_id=amo_id,
                keys=keys,
                action="skip",
                reason="no_valid_keys",
            )

        link = get_link(self.db_session, str(amo_id)) if amo_id is not None else None
        mapped_resource = link.google_resource_name if link else None

        group_resource = await self._ensure_group()
        candidates = await self._find_candidates(keys, mapped_resource)

        context = MatchContext(
            amo_contact_id=amo_id,
            group_resource_name=group_resource,
            mapped_resource_name=mapped_resource,
        )

        preflight_blocked = bool(candidates)
        primary = choose_primary(candidates, keys, context) if candidates else None
        duplicates = (
            [c for c in candidates if primary and c.resource_name != primary.resource_name]
            if candidates
            else []
        )

        if primary is None:
            if not candidates:
                action = "create"
                reason = "no_candidates"
            else:
                action = "create"
                reason = "no_primary"
        elif duplicates and self.auto_merge:
            action = "merge"
            reason = "duplicates_detected"
        else:
            action = "update"
            reason = "single_candidate" if not duplicates else "duplicates_skip_merge"

        info = [
            SyncCandidateInfo(
                resource_name=c.resource_name,
                in_group=c.in_group(group_resource),
                has_external_id=c.has_external_id(amo_contact_id=amo_id),
                matched_phones=sorted(c.matched_phones),
                matched_emails=sorted(c.matched_emails),
            )
            for c in candidates
        ]

        return SyncPlan(
            contact=contact,
            amo_contact_id=amo_id,
            keys=keys,
            action=action,
            reason=reason,
            candidates=candidates,
            primary=primary,
            duplicates=duplicates,
            mapped_resource_name=mapped_resource,
            group_resource_name=group_resource,
            candidate_info=info,
            preflight_blocked_create=preflight_blocked and action != "create",
        )

    async def apply(self, plan: SyncPlan) -> SyncResult:
        attempt = 0
        current_plan = plan
        while True:
            try:
                result = await self._apply_once(current_plan)
            except RecoverableSyncError as exc:
                attempt += 1
                if attempt > 3:
                    raise
                logger.warning(
                    "apply.retry_after_error",
                    extra={
                        "amo_contact_id": current_plan.amo_contact_id,
                        "reason": exc.reason,
                        "attempt": attempt,
                    },
                )
                current_plan = await self.plan(current_plan.contact)
                continue
            else:
                return result

    async def _apply_once(self, plan: SyncPlan) -> SyncResult:
        if plan.action == "skip":
            return SyncResult("skipped", None, reason=plan.reason)

        if plan.preflight_blocked_create:
            logger.info(
                "apply.preflight_blocked_create",
                extra={
                    "amo_contact_id": plan.amo_contact_id,
                    "candidates": [c.resource_name for c in plan.candidates],
                },
            )

        if plan.action == "create":
            resource = await self._create_contact(plan)
            if plan.amo_contact_id is not None and resource:
                save_link(self.db_session, str(plan.amo_contact_id), resource)
            return SyncResult("created", resource, reason=plan.reason)

        primary = plan.primary
        if not primary:
            raise RecoverableSyncError("missing_primary")

        if plan.action == "merge" and plan.duplicates:
            merged_primary = await self._merge_duplicates(plan, primary)
            resource = merged_primary.resource_name
            if plan.amo_contact_id is not None and resource:
                save_link(self.db_session, str(plan.amo_contact_id), resource)
            return SyncResult(
                "merged",
                resource,
                primary=resource,
                merged=[c.resource_name for c in plan.duplicates],
            )

        updated_resource = await self._update_contact(plan, primary)
        if plan.amo_contact_id is not None and updated_resource:
            save_link(self.db_session, str(plan.amo_contact_id), updated_resource)
        return SyncResult("updated", updated_resource, reason=plan.reason)

    async def _find_candidates(
        self, keys: MatchKeys, mapped_resource: Optional[str]
    ) -> List[MatchCandidate]:
        candidates = await search_google_candidates(keys)
        resource_map = {c.resource_name: c for c in candidates}
        if mapped_resource and mapped_resource not in resource_map:
            try:
                person = await google_client.get_contact(
                    mapped_resource, person_fields=PERSON_FIELDS
                )
            except Exception:
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
        await self._ensure_group()
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
        plan = SyncPlan(
            contact={},
            amo_contact_id=amo_contact_id,
            keys=keys,
            action="merge",
            reason="manual_merge",
            candidates=candidates,
            primary=primary,
            duplicates=duplicates,
            mapped_resource_name=mapped_resource,
            group_resource_name=self.group_resource_name,
        )
        merged_primary = await self._merge_duplicates(plan, primary)
        if amo_contact_id is not None:
            save_link(self.db_session, str(amo_contact_id), merged_primary.resource_name)
        return {
            "merged": len(duplicates),
            "primary": merged_primary.resource_name,
            "deleted": [c.resource_name for c in duplicates],
        }

    async def _merge_duplicates(
        self, plan: SyncPlan, primary: MatchCandidate
    ) -> MatchCandidate:
        try:
            merged_primary = await merge_contacts(
                primary,
                plan.duplicates,
                keys=plan.keys,
                group_resource_name=plan.group_resource_name,
                db_session=self.db_session,
            )
        except MissingEtagError as exc:
            raise RecoverableSyncError(f"missing_etag:{exc.resource_name}") from exc
        return merged_primary

    async def _update_contact(
        self, plan: SyncPlan, primary: MatchCandidate
    ) -> Optional[str]:
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
        new_emails = plan.keys.emails
        new_phones = plan.keys.phones
        need_emails = bool(new_emails - existing_emails)
        need_phones = bool(new_phones - existing_phones)
        current_name = ""
        if primary.person.get("names"):
            current_name = primary.person["names"][0].get("displayName") or ""
        desired_name = (plan.contact.get("name") or "").strip()
        need_name = bool(desired_name and desired_name != current_name)
        need_group = bool(
            plan.group_resource_name and not primary.in_group(plan.group_resource_name)
        )
        if not any([need_emails, need_phones, need_name, need_group]):
            reason: List[str] = []
            if not need_name:
                reason.append("name")
            if not need_phones:
                reason.append("phones")
            if not need_emails:
                reason.append("emails")
            if plan.group_resource_name and not need_group:
                reason.append("group")
            return primary.resource_name

        amo_person: Dict[str, Any] = {}
        if new_phones:
            amo_person["phoneNumbers"] = [
                {"value": phone} for phone in sorted(new_phones)
            ]
        if new_emails:
            amo_person["emailAddresses"] = [
                {"value": email} for email in sorted(new_emails)
            ]
        persons = [amo_person] if amo_person else []
        payload = union_fields(
            primary.person,
            persons,
            ensure_group=plan.group_resource_name,
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

        amo_contact_id = plan.amo_contact_id
        if amo_contact_id is not None:
            amo_value = str(amo_contact_id)
            payload["externalIds"] = [{"value": amo_value, "type": "amo_id"}]
            payload["clientData"] = [{"key": "amo_id", "value": amo_value}]
            update_fields.update({"externalIds", "clientData"})

        etag = primary.person.get("etag")
        if not etag:
            raise RecoverableSyncError("missing_etag")
        try:
            updated = await google_client.update_contact(
                primary.resource_name,
                payload,
                update_person_fields=sorted(update_fields),
                etag=etag,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {404, 410, 412}:
                raise RecoverableSyncError(f"update_failed:{status}") from exc
            raise
        return updated.get("resourceName") if isinstance(updated, dict) else None

    async def _create_contact(self, plan: SyncPlan) -> Optional[str]:
        payload: Dict[str, Any] = {
            "name": plan.contact.get("name"),
            "phones": sorted(plan.keys.phones),
            "emails": sorted(plan.keys.emails),
            "external_id": plan.amo_contact_id,
        }
        if plan.group_resource_name:
            payload["memberships"] = [
                {
                    "contactGroupMembership": {
                        "contactGroupResourceName": plan.group_resource_name
                    }
                }
            ]
        created = await create_contact(payload)
        resource_name = created.get("resourceName") if isinstance(created, dict) else None
        if not resource_name:
            return resource_name
        final_resource = await self._post_create_merge(plan, resource_name)
        return final_resource

    async def _post_create_merge(
        self, plan: SyncPlan, resource_name: str
    ) -> Optional[str]:
        candidates = await search_google_candidates(plan.keys)
        candidate_map = {candidate.resource_name: candidate for candidate in candidates}

        if resource_name not in candidate_map:
            try:
                person = await google_client.get_contact(
                    resource_name,
                    person_fields=PERSON_FIELDS,
                )
            except Exception:
                logger.warning(
                    "postcreate.fetch_new_contact_failed",
                    extra={"resource_name": resource_name},
                    exc_info=True,
                )
            else:
                candidate = build_candidate_from_person(person, plan.keys)
                if candidate is not None:
                    candidate_map[candidate.resource_name] = candidate

        if len(candidate_map) <= 1:
            return resource_name

        primary = candidate_map.get(resource_name)
        if primary is None:
            return resource_name

        existing_with_external: Optional[MatchCandidate] = None
        if plan.amo_contact_id is not None:
            for candidate in candidate_map.values():
                if candidate.resource_name == resource_name:
                    continue
                if candidate.has_external_id(amo_contact_id=plan.amo_contact_id):
                    existing_with_external = candidate
                    break

        if existing_with_external is not None:
            primary = existing_with_external

        duplicates = [
            candidate
            for candidate in candidate_map.values()
            if candidate.resource_name != primary.resource_name
        ]

        if not duplicates:
            return primary.resource_name

        try:
            merged_primary = await merge_contacts(
                primary,
                duplicates,
                keys=plan.keys,
                group_resource_name=plan.group_resource_name,
                db_session=self.db_session,
            )
        except MissingEtagError:
            logger.warning(
                "postcreate.merge_missing_etag",
                extra={"resource_name": primary.resource_name},
            )
            return primary.resource_name

        logger.info(
            "postcreate.merge_performed",
            extra={
                "amo_contact_id": plan.amo_contact_id,
                "primary": merged_primary.resource_name,
                "duplicates": [c.resource_name for c in duplicates],
            },
        )
        if plan.amo_contact_id is not None:
            save_link(
                self.db_session,
                str(plan.amo_contact_id),
                merged_primary.resource_name,
            )
        remap_google_links(
            self.db_session,
            merged_primary.resource_name,
            [c.resource_name for c in duplicates],
        )
        return merged_primary.resource_name


__all__ = ["SyncEngine", "SyncPlan", "SyncResult", "RecoverableSyncError"]
