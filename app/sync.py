from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException

from app import amocrm, google_people
from app.config import settings
from app.utils import normalize_email, normalize_phone, unique

logger = logging.getLogger(__name__)


async def fetch_amo_contacts(limit: int, since_days: Optional[int] = None) -> List[Dict[str, Any]]:
    token = settings.amo_long_lived_token
    base_url = settings.amo_base_url.rstrip("/")
    if not token or not base_url:
        raise HTTPException(status_code=500, detail="AmoCRM settings missing")
    url = f"{base_url}/api/v4/contacts"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": limit}
    if since_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        params["filter[updated_at][from]"] = since.isoformat().replace("+00:00", "Z")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"AmoCRM API error: {resp.text}")
    data = resp.json().get("_embedded", {}).get("contacts", [])
    items: List[Dict[str, Any]] = []
    for c in data:
        parsed = amocrm.extract_name_and_fields(c)
        items.append(
            {
                "id": c.get("id"),
                "name": parsed["name"],
                "emails": parsed["emails"],
                "phones": parsed["phones"],
            }
        )
    return items


async def fetch_google_contacts(
    limit: int,
    since_days: Optional[int] = None,
    amo_contacts: Optional[List[Dict[str, Any]]] = None,
    list_existing: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    counters: Dict[str, int] = {"requests": 0, "considered": 0, "found": 0}
    contacts_map: Dict[str, Dict[str, Any]] = {}
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=since_days)
        if since_days is not None
        else None
    )

    if list_existing:
        listed = await google_people.list_contacts(limit, since_days, counters)
        for c in listed:
            emails = [c.email] if c.email else []
            phones = [c.phone] if c.phone else []
            contacts_map[c.resource_id] = {
                "resourceName": c.resource_id,
                "name": c.name,
                "emails": emails,
                "phones": phones,
            }

    if amo_contacts:
        seen: set[str] = set()
        for amo_c in amo_contacts:
            keys = [normalize_email(e) for e in amo_c.get("emails", []) if e]
            keys += [normalize_phone(p) for p in amo_c.get("phones", []) if p]
            for key in set(keys):
                if key in seen:
                    continue
                seen.add(key)
                found = await google_people.search_contacts(key, counters)
                for c in found:
                    if cutoff is not None and c.update_time is not None:
                        if c.update_time.tzinfo is None:
                            continue
                        if c.update_time < cutoff:
                            continue
                    if c.resource_id not in contacts_map:
                        emails = [c.email] if c.email else []
                        phones = [c.phone] if c.phone else []
                        contacts_map[c.resource_id] = {
                            "resourceName": c.resource_id,
                            "name": c.name,
                            "emails": emails,
                            "phones": phones,
                        }
                        counters["found"] += 1

    return list(contacts_map.values()), counters


def _prepare_contacts(contacts: List[Dict[str, Any]], id_key: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    with_keys: List[Dict[str, Any]] = []
    without_keys: List[Dict[str, Any]] = []
    for c in contacts:
        emails = unique([normalize_email(e) for e in c.get("emails", []) if e])
        phones = unique([normalize_phone(p) for p in c.get("phones", []) if p])
        if emails or phones:
            c = {id_key: c.get(id_key), "name": c.get("name") or "", "emails": emails, "phones": phones}
            with_keys.append(c)
        else:
            without_keys.append(c)
    return with_keys, without_keys


def dry_run_compare(
    amo_contacts: List[Dict[str, Any]],
    google_contacts: List[Dict[str, Any]],
    direction: str,
) -> Dict[str, Any]:
    amo_with_keys, amo_no_keys = _prepare_contacts(amo_contacts, "id")
    google_with_keys, google_no_keys = _prepare_contacts(google_contacts, "resourceName")

    amo_map: Dict[str, Dict[str, Any]] = {}
    for contact in amo_with_keys:
        for key in contact["emails"] + contact["phones"]:
            amo_map.setdefault(key, contact)

    google_map: Dict[str, Dict[str, Any]] = {}
    for contact in google_with_keys:
        for key in contact["emails"] + contact["phones"]:
            google_map.setdefault(key, contact)

    matched_google: set[str] = set()
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    amo_only: List[Dict[str, Any]] = []
    for amo_c in amo_with_keys:
        counterpart = None
        keys = amo_c["emails"] + amo_c["phones"]
        for k in keys:
            g = google_map.get(k)
            if g and g["resourceName"] not in matched_google:
                counterpart = g
                break
        if counterpart:
            pairs.append((amo_c, counterpart))
            matched_google.add(counterpart["resourceName"])
        else:
            amo_only.append(amo_c)

    google_only = [c for c in google_with_keys if c["resourceName"] not in matched_google]

    def _pair_diff(amo_c: Dict[str, Any], g_c: Dict[str, Any]) -> Dict[str, Any]:
        amo_emails = set(amo_c["emails"])
        g_emails = set(g_c["emails"])
        amo_phones = set(amo_c["phones"])
        g_phones = set(g_c["phones"])
        return {
            "name_changed": (amo_c.get("name") or "") != (g_c.get("name") or ""),
            "missing_emails": list(amo_emails - g_emails),
            "missing_phones": list(amo_phones - g_phones),
            "extra_emails": list(g_emails - amo_emails),
            "extra_phones": list(g_phones - amo_phones),
        }

    updates_preview = []
    amo_to_google_updates = 0
    google_to_amo_updates = 0
    for amo_c, g_c in pairs:
        diff = _pair_diff(amo_c, g_c)
        if diff["name_changed"] or diff["missing_emails"] or diff["missing_phones"]:
            amo_to_google_updates += 1
        if diff["name_changed"] or diff["extra_emails"] or diff["extra_phones"]:
            google_to_amo_updates += 1
        if (
            diff["name_changed"]
            or diff["missing_emails"]
            or diff["missing_phones"]
            or diff["extra_emails"]
            or diff["extra_phones"]
        ) and len(updates_preview) < 5:
            updates_preview.append(
                {
                    "amo": amo_c,
                    "google": g_c,
                    "diff": {
                        "name_changed": diff["name_changed"],
                        "missing_emails": diff["missing_emails"],
                        "missing_phones": diff["missing_phones"],
                    },
                }
            )

    result = {
        "amo": {
            "fetched": len(amo_contacts),
            "with_keys": len(amo_with_keys),
            "skipped_no_keys": len(amo_no_keys),
        },
        "google": {
            "fetched": len(google_contacts),
            "with_keys": len(google_with_keys),
            "skipped_no_keys": len(google_no_keys),
        },
        "match": {
            "pairs": len(pairs),
            "amo_only": len(amo_only),
            "google_only": len(google_only),
        },
        "actions": {
            "amo_to_google": {
                "create": len(amo_only) if direction in ("both", "amo-to-google") else 0,
                "update": amo_to_google_updates if direction in ("both", "amo-to-google") else 0,
            },
            "google_to_amo": {
                "create": len(google_only) if direction in ("both", "google-to-amo") else 0,
                "update": google_to_amo_updates if direction in ("both", "google-to-amo") else 0,
            },
        },
        "samples": {
            "amo_only": amo_only[:5],
            "google_only": google_only[:5],
            "updates_preview": updates_preview[:5],
        },
    }
    return result


def build_google_lookup(contacts: List[Dict[str, Any]]) -> Dict[str, set[str]]:
    emails: set[str] = set()
    phones: set[str] = set()
    for c in contacts:
        emails.update(normalize_email(e) for e in c.get("emails", []) if e)
        phones.update(normalize_phone(p) for p in c.get("phones", []) if p)
    return {"emails": emails, "phones": phones}


def is_existing_in_google(amo_contact: Dict[str, Any], lookup: Dict[str, set[str]]) -> bool:
    for e in amo_contact.get("emails", []):
        if normalize_email(e) in lookup["emails"]:
            return True
    for p in amo_contact.get("phones", []):
        if normalize_phone(p) in lookup["phones"]:
            return True
    return False


async def apply_contacts_to_google(limit: int, since_days: int) -> Dict[str, Any]:
    amo_contacts = await fetch_amo_contacts(limit, since_days)

    created_samples: List[Dict[str, Any]] = []
    updated_samples: List[Dict[str, Any]] = []
    skipped_samples: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    created = 0
    updated = 0
    skip_existing = 0
    processed = 0

    for contact in amo_contacts:
        if processed >= limit:
            break
        processed += 1
        try:
            existing = None
            keys = [normalize_email(e) for e in contact.get("emails", []) if e]
            keys += [normalize_phone(p) for p in contact.get("phones", []) if p]
            for key in keys:
                existing = await google_people.search_contact(key)
                if existing:
                    break
            sample = {
                "amo_id": contact.get("id"),
                "name": contact.get("name"),
                "phones": contact.get("phones", []),
                "emails": contact.get("emails", []),
            }
            if existing:
                resource = existing.get("resourceName", "")
                sample["google_resource_name"] = resource
                g_emails = {normalize_email(e) for e in existing.get("emails", [])}
                g_phones = {normalize_phone(p) for p in existing.get("phones", [])}
                missing_emails = [
                    normalize_email(e)
                    for e in contact.get("emails", [])
                    if normalize_email(e) not in g_emails
                ]
                missing_phones = [
                    normalize_phone(p)
                    for p in contact.get("phones", [])
                    if normalize_phone(p) not in g_phones
                ]
                current_name = ""
                if existing.get("names"):
                    current_name = existing["names"][0].get("displayName") or ""
                new_name = contact.get("name") or ""
                need_name = new_name != current_name and new_name
                if missing_emails or missing_phones or need_name:
                    data: Dict[str, Any] = {"external_id": contact.get("id")}
                    if need_name:
                        data["name"] = new_name
                    if missing_emails:
                        data["emails"] = list(g_emails | set(missing_emails))
                    if missing_phones:
                        data["phones"] = list(g_phones | set(missing_phones))
                    await google_people.update_contact(resource, data)
                    updated += 1
                    if len(updated_samples) < 5:
                        updated_samples.append(sample)
                else:
                    skip_existing += 1
                    if len(skipped_samples) < 5:
                        skipped_samples.append(sample)
            else:
                resp = await google_people.upsert_contact_by_external_id(contact["id"], contact)
                resource = resp.get("resourceName", "")
                sample["google_resource_name"] = resource
                created += 1
                if len(created_samples) < 5:
                    created_samples.append(sample)
        except Exception as e:  # pragma: no cover - network errors
            errors.append(
                {
                    "amo_id": contact.get("id"),
                    "reason": "google_api_error",
                    "message": str(e),
                }
            )

    return {
        "direction": "to_google",
        "limit": limit,
        "processed": processed,
        "created": created,
        "updated": updated,
        "skip_existing": skip_existing,
        "samples": {
            "created": created_samples,
            "updated": updated_samples,
            "skip_existing": skipped_samples,
        },
        "errors": errors,
    }
