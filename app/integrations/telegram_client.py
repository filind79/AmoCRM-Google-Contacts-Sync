from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def telegram_alerts_enabled() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def send_telegram_alert(message: str) -> bool:
    if not telegram_alerts_enabled():
        logger.info("telegram_alert.skipped_not_configured")
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
    }
    try:
        response = httpx.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as exc:  # pragma: no cover - network path
        logger.warning("telegram_alert.failed error=%s", exc)
        return False

    logger.info("telegram_alert.sent")
    return True
