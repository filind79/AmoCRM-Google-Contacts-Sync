from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from json import JSONDecodeError
import re
from typing import Any, Dict, List, Mapping, Set
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import settings
from app.pending_sync_worker import enqueue_contact, pending_sync_worker

router = APIRouter()

_RECENT_WEBHOOK_EVENTS = deque(maxlen=10)


def _record_webhook_event(event: str, contact_id: int) -> None:
    _RECENT_WEBHOOK_EVENTS.appendleft(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "contact_id": contact_id,
        }
    )


def get_recent_webhook_events() -> List[Dict[str, Any]]:
    return list(_RECENT_WEBHOOK_EVENTS)


def clear_recent_webhook_events() -> None:
    _RECENT_WEBHOOK_EVENTS.clear()


ACCEPTED_AUTH_SOURCES = ["X-Webhook-Secret", "X-Debug-Secret", "?token"]


def _is_authorized(
    webhook_header: str | None,
    token: str | None,
    debug_header: str | None,
) -> bool:
    webhook_secret = settings.webhook_secret
    debug_secret = settings.debug_secret

    if webhook_secret and (webhook_header == webhook_secret or token == webhook_secret):
        return True

    if debug_secret and debug_header == debug_secret:
        return True

    return False


def _unauthorized_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized", "accepted": ACCEPTED_AUTH_SOURCES},
    )


def _extract_contact_ids(payload: Dict[str, Any]) -> List[int]:
    ids: Set[int] = set()
    direct = payload.get("contact_id")
    if direct is not None:
        try:
            ids.add(int(direct))
        except (TypeError, ValueError):
            logger.warning("webhook.invalid_contact_id", contact_id=direct)
    batch = payload.get("contact_ids")
    if isinstance(batch, list):
        for item in batch:
            try:
                ids.add(int(item))
            except (TypeError, ValueError):
                logger.warning("webhook.invalid_contact_id", contact_id=item)
    contacts_section = payload.get("contacts")
    if isinstance(contacts_section, dict):
        for key in ("add", "update"):
            events = contacts_section.get(key) or []
            for event in events:
                if isinstance(event, dict) and event.get("id") is not None:
                    try:
                        ids.add(int(event["id"]))
                    except (TypeError, ValueError):
                        logger.warning("webhook.invalid_contact_id", contact_id=event.get("id"))
    return [cid for cid in ids if cid > 0]


def _guess_event_name(payload: Dict[str, Any], contact_id: int) -> str:
    event = payload.get("event")
    if isinstance(event, str) and event:
        return event

    contacts_section = payload.get("contacts")
    if isinstance(contacts_section, dict):
        for key in ("add", "update", "delete"):
            events = contacts_section.get(key)
            if not isinstance(events, list):
                continue
            for item in events:
                if not isinstance(item, dict):
                    continue
                try:
                    item_id = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                if item_id == contact_id:
                    return f"contacts.{key}"

    return "contact_updated"


_FORM_CONTACT_ID_PATTERN = re.compile(r"^contacts\[(add|update)\]\[\d+\]\[id\]$")


def _extract_contact_ids_from_form(form_data: Mapping[str, List[str]]) -> List[int]:
    ids: Set[int] = set()
    for key, values in form_data.items():
        if not _FORM_CONTACT_ID_PATTERN.match(key):
            continue
        for value in values:
            try:
                contact_id = int(value)
            except (TypeError, ValueError):
                logger.warning("webhook.invalid_contact_id", contact_id=value)
                continue
            if contact_id > 0:
                ids.add(contact_id)
    return list(ids)


@router.post("/webhook/amo")
async def webhook_amo(
    request: Request,
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    token: str | None = Query(None),
    x_debug_secret: str | None = Header(None, alias="X-Debug-Secret"),
) -> JSONResponse:
    if not _is_authorized(x_webhook_secret, token, x_debug_secret):
        return _unauthorized_response()

    contact_ids: Set[int] = set()
    parsed_source: str | None = None
    payload: Dict[str, Any] = {}

    try:
        json_payload = await request.json()
    except (JSONDecodeError, ValueError, UnicodeDecodeError):
        json_payload = None
    except Exception:  # pragma: no cover - defensive guard
        json_payload = None
    if isinstance(json_payload, dict):
        payload = json_payload
        contact_ids.update(_extract_contact_ids(payload))
        if contact_ids:
            parsed_source = "json"

    body = await request.body()
    if not contact_ids and body:
        form = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
        contact_ids.update(_extract_contact_ids_from_form(form))
        if contact_ids:
            parsed_source = "form"

    if contact_ids:
        logger.info("webhook.parsed", count=len(contact_ids), source=parsed_source)
    else:
        logger.warning(
            "webhook.empty_payload",
            content_type=(request.headers.get("content-type", "")[:50]),
            body_length=len(body),
        )
        return JSONResponse(content={"queued": [], "warning": "no_contact_ids_parsed"})

    queued: List[int] = []
    event_payload: Dict[str, Any] = payload if parsed_source == "json" else {}
    for contact_id in sorted(contact_ids):
        enqueue_contact(contact_id)
        queued.append(contact_id)
        _record_webhook_event(_guess_event_name(event_payload, contact_id), contact_id)

    logger.info("webhook.queued", count=len(queued), ids=queued)
    pending_sync_worker.wake()
    return JSONResponse(content={"queued": queued})
