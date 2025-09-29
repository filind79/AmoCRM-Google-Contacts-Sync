from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence, Set

from app.services.match import normalize_email, normalize_phone


def _deduplicate_phones(persons: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    merged: List[Dict[str, Any]] = []
    for person in persons:
        for phone in person.get("phoneNumbers", []) or []:
            if not isinstance(phone, dict):
                continue
            value = phone.get("value")
            if not value:
                continue
            normalized = normalize_phone(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            entry = {"value": normalized}
            if phone.get("type"):
                entry["type"] = phone["type"]
            if phone.get("metadata"):
                entry["metadata"] = phone["metadata"]
            if phone.get("formattedType"):
                entry["formattedType"] = phone["formattedType"]
            merged.append(entry)
    return merged


def _deduplicate_emails(persons: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    merged: List[Dict[str, Any]] = []
    for person in persons:
        for email in person.get("emailAddresses", []) or []:
            if not isinstance(email, dict):
                continue
            value = email.get("value")
            if not value:
                continue
            normalized = normalize_email(value)
            if normalized in seen:
                continue
            seen.add(normalized)
            entry = {"value": value}
            if email.get("type"):
                entry["type"] = email["type"]
            if email.get("metadata"):
                entry["metadata"] = email["metadata"]
            merged.append(entry)
    return merged


def _merge_memberships(
    persons: Sequence[Dict[str, Any]],
    *,
    ensure_group: Optional[str] = None,
) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    merged: List[Dict[str, Any]] = []
    for person in persons:
        for membership in person.get("memberships", []) or []:
            if not isinstance(membership, dict):
                continue
            data = membership.get("contactGroupMembership")
            if not isinstance(data, dict):
                continue
            group_name = data.get("contactGroupResourceName")
            if not group_name or group_name in seen:
                continue
            seen.add(group_name)
            merged.append(deepcopy(membership))
    if ensure_group and ensure_group not in seen:
        merged.append(
            {
                "contactGroupMembership": {
                    "contactGroupResourceName": ensure_group,
                }
            }
        )
    return merged


def _merge_biographies(primary: Dict[str, Any], others: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    base_values: List[Dict[str, Any]] = []
    seen_texts: Set[str] = set()
    for entry in primary.get("biographies", []) or []:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not value or value in seen_texts:
            continue
        seen_texts.add(value)
        base_values.append(deepcopy(entry))

    for person in others:
        note = person.get("biographies", []) or []
        if not note:
            continue
        value = None
        for entry in note:
            if isinstance(entry, dict) and entry.get("value"):
                value = entry["value"]
                break
        if not value or value in seen_texts:
            continue
        seen_texts.add(value)
        resource_name = person.get("resourceName") or "unknown"
        combined = f"[Merged from {resource_name}]\n{value}"
        base_values.append({"value": combined})

    return base_values


def union_fields(
    primary: Dict[str, Any],
    others: Sequence[Dict[str, Any]],
    *,
    ensure_group: Optional[str] = None,
) -> Dict[str, Any]:
    persons: List[Dict[str, Any]] = [primary] + list(others)
    merged: Dict[str, Any] = {}

    phone_numbers = _deduplicate_phones(persons)
    if phone_numbers:
        merged["phoneNumbers"] = phone_numbers

    email_addresses = _deduplicate_emails(persons)
    if email_addresses:
        merged["emailAddresses"] = email_addresses

    memberships = _merge_memberships(persons, ensure_group=ensure_group)
    if memberships:
        merged["memberships"] = memberships

    biographies = _merge_biographies(primary, others)
    if biographies:
        merged["biographies"] = biographies

    if primary.get("names"):
        merged["names"] = deepcopy(primary["names"])

    return merged
