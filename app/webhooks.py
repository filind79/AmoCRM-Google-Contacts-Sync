from __future__ import annotations

from typing import Any, Dict, List, Set

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import settings
from app.pending_sync_worker import enqueue_contact, pending_sync_worker

router = APIRouter()


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


@router.post("/webhook/amo")
async def webhook_amo(
    payload: Dict[str, Any],
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    token: str | None = Query(None),
    x_debug_secret: str | None = Header(None, alias="X-Debug-Secret"),
) -> JSONResponse:
    if not _is_authorized(x_webhook_secret, token, x_debug_secret):
        return _unauthorized_response()
    contact_ids = _extract_contact_ids(payload)
    if not contact_ids:
        raise HTTPException(status_code=400, detail="No contact ids supplied")

    queued: List[int] = []
    for contact_id in contact_ids:
        enqueue_contact(contact_id)
        queued.append(contact_id)

    logger.info("webhook.queued", count=len(queued), ids=queued)
    pending_sync_worker.wake()
    return JSONResponse(content={"queued": queued})
