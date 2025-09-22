"""Lightweight Google People API client used by the service.

The module exposes helper functions for listing contacts and updating/creating
them.  Authentication is delegated to :mod:`app.google_auth` which ensures that
an up to date access token is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncio
from collections import deque
import contextvars
import logging
import math
import os
import random
import time
from email.utils import parsedate_to_datetime

import httpx

from app.google_auth import (
    GoogleAuthError,
    force_refresh_google_access_token,
    get_valid_google_access_token,
)
from app.storage import get_session
from app.utils import normalize_email, normalize_phone, unique


GOOGLE_API_BASE = "https://people.googleapis.com/v1"

GOOGLE_RPM = int(os.getenv("GOOGLE_RPM", "20"))


logger = logging.getLogger(__name__)


class GoogleRateLimitError(Exception):
    """Raised when Google People API quota is exceeded."""

    def __init__(self, retry_after: int, payload: Optional[Dict[str, Any]] | None = None) -> None:
        self.retry_after = retry_after
        self.payload = payload or {}


class RateLimitError(Exception):
    """Legacy error used by tests for direct contact creation."""

    def __init__(self, retry_after: Optional[int] = None):
        super().__init__("rate_limited")
        self.retry_after = retry_after


class _RateLimiter:
    def __init__(self, rpm: int) -> None:
        self.rpm = rpm
        self._calls: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) < self.rpm:
                self._calls.append(now)
                return
            await asyncio.sleep(60 - (now - self._calls[0]))


_rate_limiter = _RateLimiter(GOOGLE_RPM)
_google_metrics_ctx: contextvars.ContextVar[Optional[Dict[str, int]]] = contextvars.ContextVar(
    "google_people_metrics",
    default=None,
)


def bind_metrics(metrics: Dict[str, int]) -> contextvars.Token:
    """Attach a metrics dictionary to track Google API usage."""

    return _google_metrics_ctx.set(metrics)


def reset_metrics(token: contextvars.Token) -> None:
    """Restore previous metrics binding."""

    _google_metrics_ctx.reset(token)


def _increment_metric(key: str, amount: int = 1) -> None:
    metrics = _google_metrics_ctx.get()
    if metrics is None:
        return
    metrics[key] = metrics.get(key, 0) + amount


def _is_resource_exhausted(resp: httpx.Response) -> bool:
    try:
        return resp.json().get("error", {}).get("status") == "RESOURCE_EXHAUSTED"
    except Exception:
        return False


def _retry_after_seconds(resp: httpx.Response) -> Optional[float]:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(header)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    reset_header = resp.headers.get("X-RateLimit-Reset")
    if reset_header:
        try:
            reset_ts = float(reset_header)
        except ValueError:
            return None
        return max(0.0, reset_ts - time.time())
    return None


async def _request(method: str, url: str, **kwargs) -> httpx.Response:
    max_attempts = 5
    max_wait = 60
    attempt = 0
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            await _rate_limiter.acquire()
            call = getattr(client, method.lower(), None)
            if call is None:
                resp = await client.request(method, url, **kwargs)
            else:
                resp = await call(url, **kwargs)
            _increment_metric("google_requests")
            try:
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                is_rate = code == 429 or (code == 403 and _is_resource_exhausted(e.response))
                if is_rate:
                    _increment_metric("rate_limit_hits")
                    retry_after = _retry_after_seconds(e.response)
                    backoff = min(max_wait, 2 ** attempt)
                    wait_for = max(retry_after or 0, backoff)
                    wait_for = min(max_wait, wait_for) + random.uniform(0, 1)
                    logger.warning(
                        "google_people.rate_limit",  # noqa: TRY400
                        extra={
                            "wait": round(wait_for, 2),
                            "attempt": attempt + 1,
                            "status": code,
                        },
                    )
                    if attempt >= max_attempts - 1:
                        retry = int(math.ceil(wait_for)) or 1
                        raise GoogleRateLimitError(retry) from e
                    _increment_metric("retries")
                    await asyncio.sleep(wait_for)
                    attempt += 1
                    continue
                raise


@dataclass
class Contact:
    resource_id: str
    name: str
    email: Optional[str]
    phone: Optional[str]
    etag: Optional[str] = None
    update_time: Optional[datetime] = None


async def _token_headers(session) -> Dict[str, str]:
    token = await get_valid_google_access_token(session)
    return {"Authorization": f"Bearer {token}"}



def _parse_rfc3339(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _parse_update_time(person: Dict[str, Any]) -> Optional[datetime]:
    sources = person.get("metadata", {}).get("sources", [])
    times = []
    for src in sources:
        ts = src.get("updateTime")
        if ts:
            try:
                times.append(_parse_rfc3339(ts))
            except ValueError:
                continue
    return max(times) if times else None


async def list_contacts(
    limit: int,
    since_days: Optional[int] = None,
    counters: Optional[Dict[str, int]] = None,
    *,
    fast: bool = False,
) -> List[Contact]:
    """Fetch a list of contacts from Google People API with pagination."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        url = f"{GOOGLE_API_BASE}/people/me/connections"
        collected: List[Contact] = []
        page_token: Optional[str] = None
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=since_days)
            if since_days is not None
            else None
        )
        while len(collected) < limit:
            remaining = limit if fast else max(1, limit - len(collected))
            params: Dict[str, Any] = {
                "personFields": "names,emailAddresses,phoneNumbers,metadata",
                "pageSize": min(200, remaining),
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = await _request("GET", url, params=params, headers=headers)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    new_token = await force_refresh_google_access_token(session)
                    headers["Authorization"] = f"Bearer {new_token}"
                    try:
                        resp = await _request("GET", url, params=params, headers=headers)
                    except httpx.HTTPStatusError:
                        raise GoogleAuthError("unauthorised", "/auth/google/start")
                else:
                    raise
            if counters is not None:
                counters["requests"] = counters.get("requests", 0) + 1
                counters["pages"] = counters.get("pages", 0) + 1
            data = resp.json()
            persons = data.get("connections", [])
            if counters is not None:
                counters["considered"] = counters.get("considered", 0) + len(persons)
            for person in persons:
                upd = _parse_update_time(person)
                if cutoff is not None and upd is not None:
                    if upd.tzinfo is None:
                        continue
                    if upd < cutoff:
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
                        etag=person.get("etag"),
                        update_time=upd,
                    )
                )
            page_token = data.get("nextPageToken")
            if fast or not page_token:
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
        resp = await _request("GET", url, params=params, headers=headers)
        if counters is not None:
            counters["requests"] = counters.get("requests", 0) + 1
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
                etag=person.get("etag"),
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
        params = {
            "query": str(amo_contact_id),
            "readMask": "names,phoneNumbers,emailAddresses,metadata",
        }
        resp = await _request("GET", url, params=params, headers=headers)
        results = resp.json().get("results", [])
        person: Dict[str, Any] = {}
        resource_name: Optional[str] = None
        if results:
            person = results[0].get("person", {})
            resource_name = person.get("resourceName")

        etag = person.get("etag")
        body: Dict[str, Any] = {"externalIds": [external_id]}
        name_value = data.get("name")
        if not isinstance(name_value, str):
            name_value = "" if name_value is None else str(name_value)
        name_entry = {"unstructuredName": name_value, "metadata": {"primary": True}}
        include_name = True
        if resource_name and not (name_value and name_value.strip()):
            include_name = False
        if include_name:
            body["names"] = [name_entry]
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
            if etag:
                body["etag"] = etag
            update_url = f"{GOOGLE_API_BASE}/{resource_name}:updateContact"
            update_params = {
                "updatePersonFields": "names,phoneNumbers,emailAddresses,externalIds"
            }
            resp = await _request(
                "PATCH", update_url, params=update_params, headers=headers, json=body
            )
        else:
            create_url = f"{GOOGLE_API_BASE}/people:createContact"
            resp = await _request("POST", create_url, headers=headers, json=body)
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
    finally:
        session.close()

    headers["Content-Type"] = "application/json"

    name_value = data.get("name")
    if not isinstance(name_value, str):
        name_value = "" if name_value is None else str(name_value)
    body: Dict[str, Any] = {
        "names": [{"unstructuredName": name_value, "metadata": {"primary": True}}]
    }

    external_id = data.get("external_id")
    if external_id is not None:
        body["externalIds"] = [{"value": str(external_id), "type": "AMOCRM"}]

    phones = unique([normalize_phone(p) for p in data.get("phones", []) if p])
    if phones:
        body["phoneNumbers"] = [{"value": p} for p in phones]

    emails = unique([normalize_email(e) for e in data.get("emails", []) if e])
    if emails:
        body["emailAddresses"] = [{"value": e} for e in emails]

    url = f"{GOOGLE_API_BASE}/people:createContact"

    try:
        resp = await _request("POST", url, headers=headers, json=body)
    except GoogleRateLimitError as exc:
        raise RateLimitError(exc.retry_after) from exc

    return resp.json()


async def search_contact(query: str) -> Optional[Dict[str, Any]]:
    """Search a contact by phone or email and return basic fields if found."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        params = {
            "query": query,
            "readMask": "names,emailAddresses,phoneNumbers,metadata",
        }
        url = f"{GOOGLE_API_BASE}/people:searchContacts"
        resp = await _request("GET", url, params=params, headers=headers)
        results = resp.json().get("results", [])
        if not results:
            return None
        person = results[0].get("person", {})
        names = person.get("names", [])
        return {
            "resourceName": person.get("resourceName", ""),
            "etag": person.get("etag"),
            "names": names,
            "emails": [
                e.get("value")
                for e in person.get("emailAddresses", [])
                if e.get("value")
            ],
            "phones": [
                p.get("value")
                for p in person.get("phoneNumbers", [])
                if p.get("value")
            ],
        }
    finally:
        session.close()


async def update_contact(resource_name: str, etag: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing Google contact with provided fields."""

    session = get_session()
    try:
        headers = await _token_headers(session)
        headers["Content-Type"] = "application/json"
        body: Dict[str, Any] = {"resourceName": resource_name, "etag": etag}
        update_fields: List[str] = []
        name = data.get("name")
        if name is not None:
            name_str = str(name)
            if name_str.strip():
                body["names"] = [
                    {"unstructuredName": name_str, "metadata": {"primary": True}}
                ]
                update_fields.append("names")
        emails = data.get("emails")
        if emails is not None:
            body["emailAddresses"] = [{"value": e} for e in emails]
            update_fields.append("emailAddresses")
        phones = data.get("phones")
        if phones is not None:
            body["phoneNumbers"] = [{"value": p} for p in phones]
            update_fields.append("phoneNumbers")
        external_id = data.get("external_id")
        if external_id is not None:
            body["externalIds"] = [{"value": str(external_id), "type": "AMOCRM"}]
            update_fields.append("externalIds")
        if not update_fields:
            return {}
        params = {
            "updatePersonFields": "names,phoneNumbers,emailAddresses,externalIds"
        }
        url = f"{GOOGLE_API_BASE}/{resource_name}:updateContact"
        resp = await _request("PATCH", url, params=params, headers=headers, json=body)
        return resp.json()
    finally:
        session.close()

