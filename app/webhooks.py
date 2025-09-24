from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from fastapi import APIRouter, Header, HTTPException, Query
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


def _provided_secret(header: str | None, token: str | None) -> str | None:
    return header or token


def _require_secret(header: str | None, token: str | None) -> None:
    secret = settings.webhook_secret
    provided = _provided_secret(header, token)
    if not secret or provided != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


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


@router.post("/webhook/amo")
async def webhook_amo(
    payload: Dict[str, Any],
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    token: str | None = Query(None),
) -> Dict[str, Any]:
    _require_secret(x_webhook_secret, token)
    contact_ids = _extract_contact_ids(payload)
    if not contact_ids:
        raise HTTPException(status_code=400, detail="No contact ids supplied")

    queued: List[int] = []
    for contact_id in contact_ids:
        enqueue_contact(contact_id)
        queued.append(contact_id)
        _record_webhook_event(_guess_event_name(payload, contact_id), contact_id)

    logger.info("webhook.queued", count=len(queued), ids=queued)
    pending_sync_worker.wake()
    return {"queued": queued}
