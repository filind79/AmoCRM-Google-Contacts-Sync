"""Utilities for storing and refreshing Google OAuth tokens."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.config import settings
from app.core.integration_state import (
    AUTH_STATUS_NEEDS_REAUTH,
    GoogleIntegrationState,
    get_google_integration_state,
    mark_google_alert_sent,
    mark_google_auth_failure,
    mark_google_auth_ok,
    mark_google_recovery_alert_sent,
)
from app.integrations.telegram_client import send_telegram_alert, telegram_alerts_enabled
from app.storage import Token, get_token, save_token


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
REAUTH_URL = "https://amocrm-google-contacts-sync.onrender.com/auth/google/start"

logger = logging.getLogger(__name__)


@dataclass
class GoogleAuthError(Exception):
    """Raised when Google authentication fails or requires user interaction."""

    reason: str
    auth_url: Optional[str] = None


def _record_auth_ready(session, *, refreshed_at: Optional[datetime] = None) -> bool:
    changed = mark_google_auth_ok(session, now=refreshed_at)
    logger.info("google_auth.refresh_ok")
    if changed:
        logger.warning("google_auth.state_changed_to_ok")
        send_telegram_alert(
            "Google Contacts authorization restored.\n"
            "Синхронизация снова работает."
        )
        if telegram_alerts_enabled():
            mark_google_recovery_alert_sent(session)
    return changed


def _record_auth_failure(session, reason: str) -> int:
    changed, failures = mark_google_auth_failure(session, reason)
    logger.warning("google_auth.refresh_failed reason=%s failure_count=%s", reason, failures)
    if changed:
        logger.error("google_auth.state_changed_to_needs_reauth")
        send_telegram_alert(
            "Google Contacts authorization failed.\n"
            "Нужно заново авторизоваться:\n"
            f"{REAUTH_URL}"
        )
        if telegram_alerts_enabled():
            mark_google_alert_sent(session)
    return failures


async def _refresh_token(session) -> Token:
    """Refresh the Google OAuth token stored in the database."""

    token = get_token(session, "google")
    if not token or not token.refresh_token:
        _record_auth_failure(session, "refresh_unavailable")
        raise GoogleAuthError("refresh_unavailable", REAUTH_URL)

    data = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
    }

    try:
        resp = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=10)
    except httpx.RequestError as exc:  # pragma: no cover - network errors are rare
        logger.warning("google.refresh_request_error error=%s", exc)
        raise GoogleAuthError(f"network_error: {exc}")

    if resp.status_code != 200:
        _record_auth_failure(session, f"http_{resp.status_code}")
        raise GoogleAuthError("refresh_failed", REAUTH_URL)

    payload = resp.json()
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)
    new_refresh = payload.get("refresh_token") or token.refresh_token

    refresh_time = datetime.utcnow()
    expiry = refresh_time + timedelta(seconds=int(expires_in))
    save_token(
        session,
        "google",
        access_token=access_token,
        refresh_token=new_refresh,
        expiry=expiry,
        scopes=token.scopes or "",
        account_id=token.account_id,
    )
    _record_auth_ready(session, refreshed_at=refresh_time)
    return get_token(session, "google")


def _ensure_token(session) -> Token:
    token = get_token(session, "google")
    if not token:
        _record_auth_failure(session, "token_missing")
        raise GoogleAuthError("token_missing", REAUTH_URL)
    if not token.refresh_token:
        _record_auth_failure(session, "refresh_unavailable")
        raise GoogleAuthError("refresh_unavailable", REAUTH_URL)
    return token


async def get_valid_google_access_token(session, *, force_refresh: bool = False) -> str:
    token = _ensure_token(session)

    now = datetime.utcnow()
    should_refresh = force_refresh or not token.expiry or token.expiry <= now + timedelta(seconds=60)
    if should_refresh:
        token = await _refresh_token(session)

    return token.access_token


async def force_refresh_google_access_token(session) -> str:
    token = await _refresh_token(session)
    return token.access_token


@dataclass
class GoogleAuthState:
    auth_status: str
    last_refresh_at: Optional[datetime]
    expires_in: Optional[int]
    failure_count: int
    last_failure_at: Optional[datetime]
    last_error: Optional[str]
    last_alert_sent_at: Optional[datetime]
    last_recovery_alert_sent_at: Optional[datetime]


def get_google_auth_state(session) -> GoogleAuthState:
    token = get_token(session, "google")
    integration_state: GoogleIntegrationState = get_google_integration_state(session)

    expires_in: Optional[int] = None
    if token and token.expiry:
        delta = int((token.expiry - datetime.utcnow()).total_seconds())
        expires_in = delta if delta >= 0 else 0

    status = integration_state.auth_status
    if not token or not token.refresh_token:
        status = AUTH_STATUS_NEEDS_REAUTH

    return GoogleAuthState(
        auth_status=status,
        last_refresh_at=integration_state.last_refresh_at,
        expires_in=expires_in,
        failure_count=integration_state.failure_count,
        last_failure_at=integration_state.last_failure_at,
        last_error=integration_state.last_error,
        last_alert_sent_at=integration_state.last_alert_sent_at,
        last_recovery_alert_sent_at=integration_state.last_recovery_alert_sent_at,
    )


def google_auth_needs_reauth(session) -> bool:
    state = get_google_auth_state(session)
    return state.auth_status == AUTH_STATUS_NEEDS_REAUTH


def mark_google_auth_ready(session) -> None:
    _record_auth_ready(session)
