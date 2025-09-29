from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

import logging

from app.integrations import google_client
from app.utils import normalize_email as _normalize_email
from app.utils import normalize_phone as _normalize_phone
from app.google_people import _parse_update_time


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MatchKeys:
    phones: Set[str]
    emails: Set[str]

    @classmethod
    def from_raw(cls, phones: Iterable[str], emails: Iterable[str]) -> "MatchKeys":
        phone_set = {p for value in phones if value and (p := normalize_phone(value))}
        email_set = {normalize_email(e) for e in emails if e}
        return cls(phones=phone_set, emails=email_set)

    def as_queries(self) -> List[str]:
        return list(self.phones | self.emails)

    def __bool__(self) -> bool:  # pragma: no cover - defensive guard
        return bool(self.phones or self.emails)


@dataclass(slots=True)
class MatchContext:
    amo_contact_id: Optional[int] = None
    group_resource_name: Optional[str] = None
    mapped_resource_name: Optional[str] = None


@dataclass(slots=True)
class MatchCandidate:
    resource_name: str
    person: Dict[str, Any]
    matched_phones: Set[str]
    matched_emails: Set[str]
    update_time: Optional[datetime]

    def has_exact_phone(self, keys: MatchKeys) -> bool:
        return bool(self.matched_phones & keys.phones)

    def in_group(self, group_resource_name: Optional[str]) -> bool:
        if not group_resource_name:
            return False
        memberships = self.person.get("memberships") or []
        for membership in memberships:
            data = membership.get("contactGroupMembership")
            if not isinstance(data, dict):
                continue
            if data.get("contactGroupResourceName") == group_resource_name:
                return True
        return False

    def has_external_id(self, *, amo_contact_id: Optional[int] = None) -> bool:
        entries = self.person.get("externalIds") or []
        if not isinstance(entries, list):
            return False
        target = str(amo_contact_id) if amo_contact_id is not None else None
        found_any = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "amo_id":
                continue
            value = entry.get("value")
            if target is not None:
                if value == target:
                    return True
                continue
            if value:
                found_any = True
        return found_any if target is None else False


def normalize_phone(phone: str) -> Optional[str]:
    return _normalize_phone(phone)


def normalize_email(email: str) -> str:
    return _normalize_email(email)


def build_candidate_from_person(person: Dict[str, Any], keys: MatchKeys) -> Optional[MatchCandidate]:
    resource_name = person.get("resourceName")
    if not resource_name:
        return None
    phone_values: Set[str] = set()
    for phone in person.get("phoneNumbers", []) or []:
        value = phone.get("value") if isinstance(phone, dict) else None
        if not value:
            continue
        normalized = normalize_phone(value)
        if normalized and normalized in keys.phones:
            phone_values.add(normalized)
    email_values: Set[str] = set()
    for email in person.get("emailAddresses", []) or []:
        value = email.get("value") if isinstance(email, dict) else None
        if not value:
            continue
        normalized = normalize_email(value)
        if normalized in keys.emails:
            email_values.add(normalized)
    update_time = _parse_update_time(person)
    return MatchCandidate(
        resource_name=resource_name,
        person=person,
        matched_phones=phone_values,
        matched_emails=email_values,
        update_time=update_time,
    )


async def search_google_candidates(keys: MatchKeys) -> List[MatchCandidate]:
    if not keys:
        return []

    candidate_map: Dict[str, Dict[str, Any]] = {}
    read_mask = "names,emailAddresses,phoneNumbers,metadata"
    person_fields = "names,phoneNumbers,emailAddresses,memberships,biographies,metadata"

    seen_queries: Set[str] = set()
    sources_supported = True
    other_contacts_supported = True

    async def _register(persons: Iterable[Dict[str, Any]]) -> None:
        for person in persons:
            resource_name = person.get("resourceName")
            if not resource_name:
                continue
            candidate_map.setdefault(resource_name, {})

    async def _collect(query: str) -> None:
        nonlocal sources_supported, other_contacts_supported

        if not query or query in seen_queries:
            return

        seen_queries.add(query)

        if sources_supported:
            try:
                results = await google_client.search_contacts(
                    query,
                    read_mask=read_mask,
                    sources=(
                        "READ_SOURCE_TYPE_CONTACT",
                        "READ_SOURCE_TYPE_OTHER_CONTACT",
                    ),
                )
            except Exception:  # pragma: no cover - fallback for unsupported sources
                sources_supported = False
                logger.debug(
                    "match.search_contacts_sources_failed",
                    exc_info=True,
                    extra={"query": query},
                )
            else:
                await _register(results)
                return

        results = await google_client.search_contacts(query, read_mask=read_mask)
        await _register(results)

        if not other_contacts_supported:
            return

        try:
            other_results = await google_client.search_other_contacts(
                query,
                read_mask=read_mask,
            )
        except Exception:  # pragma: no cover - fallback when access is missing
            other_contacts_supported = False
            logger.debug(
                "match.search_other_contacts_failed",
                exc_info=True,
                extra={"query": query},
            )
            return

        await _register(other_results)

    for phone in keys.phones:
        await _collect(phone)
        if phone.startswith("+") and len(phone) > 1:
            await _collect(phone[1:])
    for email in keys.emails:
        await _collect(email)

    candidates: List[MatchCandidate] = []
    for resource_name in candidate_map:
        person = await google_client.get_contact(resource_name, person_fields=person_fields)
        candidate = build_candidate_from_person(person, keys)
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def choose_primary(candidates: Sequence[MatchCandidate], keys: MatchKeys, context: MatchContext) -> Optional[MatchCandidate]:
    if not candidates:
        return None

    ordered: Sequence[MatchCandidate] = list(candidates)
    reason_parts: List[str] = []

    exact_matches = [candidate for candidate in ordered if candidate.has_exact_phone(keys)]
    if exact_matches:
        ordered = exact_matches
        reason_parts.append("exact_phone")

    amo_matches: Sequence[MatchCandidate]
    if context.amo_contact_id is not None:
        amo_matches = [
            candidate
            for candidate in ordered
            if candidate.has_external_id(amo_contact_id=context.amo_contact_id)
        ]
    else:
        amo_matches = [candidate for candidate in ordered if candidate.has_external_id()]
    if amo_matches:
        ordered = amo_matches
        reason_parts.append("external_id")

    group_matches = [candidate for candidate in ordered if candidate.in_group(context.group_resource_name)]
    if context.group_resource_name and group_matches:
        ordered = group_matches
        reason_parts.append("group")

    if context.mapped_resource_name:
        mapped = [c for c in ordered if c.resource_name == context.mapped_resource_name]
        if mapped:
            ordered = mapped
            reason_parts.append("mapping")

    def _score(candidate: MatchCandidate) -> datetime:
        if candidate.update_time and candidate.update_time.tzinfo is None:
            return candidate.update_time.replace(tzinfo=timezone.utc)
        return candidate.update_time or datetime.fromtimestamp(0, tz=timezone.utc)

    selected = max(ordered, key=_score)
    reason_parts.append("recent")

    logger.info(
        "match.primary_selected",
        extra={
            "amo_contact_id": context.amo_contact_id,
            "resource_name": selected.resource_name,
            "candidates": [c.resource_name for c in candidates],
            "reason": "|".join(reason_parts),
        },
    )
    return selected
