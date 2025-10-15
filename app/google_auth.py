"""Utilities for storing and refreshing Google OAuth tokens.

This module provides a small abstraction around the token storage in the
database.  The public function :func:`get_valid_google_access_token` returns a
usable access token, refreshing it with Google if it is about to expire.  When
refreshing fails or is impossible a :class:`GoogleAuthError` is raised so that
callers can react appropriately (e.g. by asking the user to re-authorise).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.config import settings
from app.storage import Token, get_setting, get_token, save_token, set_settings


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_STATUS_OK = "ok"
AUTH_STATUS_NEEDS_REAUTH = "needs_reauth"
SETTING_AUTH_STATUS = "google_auth_status"
SETTING_LAST_REFRESH = "google_last_refresh"
SETTING_REFRESH_FAILURES = "google_refresh_failures"
SETTING_LAST_ERROR = "google_last_refresh_error"

logger = logging.getLogger(__name__)


@dataclass
class GoogleAuthError(Exception):
    """Raised when Google authentication fails or requires user interaction."""

    reason: str
    auth_url: Optional[str] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_auth_ready(session, *, refreshed_at: Optional[datetime] = None) -> None:
    refreshed_at = refreshed_at or _utcnow()
    set_settings(
        session,
        {
            SETTING_AUTH_STATUS: AUTH_STATUS_OK,
            SETTING_LAST_REFRESH: _serialize_dt(refreshed_at),
            SETTING_REFRESH_FAILURES: "0",
            SETTING_LAST_ERROR: None,
        },
    )


def _record_auth_failure(session, reason: str) -> int:
    try:
        failures = int(get_setting(session, SETTING_REFRESH_FAILURES) or "0")
    except ValueError:
        failures = 0
    failures += 1
    payload = {
        SETTING_AUTH_STATUS: AUTH_STATUS_NEEDS_REAUTH,
        SETTING_REFRESH_FAILURES: str(failures),
        SETTING_LAST_ERROR: reason,
    }
    set_settings(session, payload)
    return failures


async def _refresh_token(session) -> Token:
    """Refresh the Google OAuth token stored in the database."""

    token = get_token(session, "google")
    if not token or not token.refresh_token:
        _record_auth_failure(session, "refresh_unavailable")
        raise GoogleAuthError("refresh_unavailable", "/auth/google/start")

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
        reason = f"http_{resp.status_code}"
        failures = _record_auth_failure(session, reason)
        if failures > 3:
            logger.error(
                "google.refresh_failed_repeatedly",
                extra={"failures": failures, "status": resp.status_code},
            )
        raise GoogleAuthError("refresh_failed", "/auth/google/start")

    payload = resp.json()
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)
    new_refresh = payload.get("refresh_token") or token.refresh_token

    refresh_time = _utcnow()
    expiry = refresh_time + timedelta(seconds=int(expires_in))
    save_token(
        session,
        "google",
        access_token=access_token,
        refresh_token=new_refresh,
        expiry=expiry.replace(tzinfo=None),
        scopes=token.scopes or "",
        account_id=token.account_id,
    )
    _record_auth_ready(session, refreshed_at=refresh_time)
    return get_token(session, "google")


def _ensure_token(session) -> Token:
    token = get_token(session, "google")
    if not token:
        _record_auth_failure(session, "token_missing")
        raise GoogleAuthError("token_missing", "/auth/google/start")
    if not token.refresh_token:
        _record_auth_failure(session, "refresh_unavailable")
        raise GoogleAuthError("refresh_unavailable", "/auth/google/start")
    return token


async def get_valid_google_access_token(session, *, force_refresh: bool = False) -> str:
    """Return a valid access token for Google APIs.

    If the current token is close to expiring (within 60 seconds) it is
    refreshed automatically.  :class:`GoogleAuthError` is raised when the token
    is missing or cannot be refreshed.
    """

    token = _ensure_token(session)

    now = datetime.utcnow()
    should_refresh = force_refresh or not token.expiry or token.expiry <= now + timedelta(seconds=60)
    if should_refresh:
        token = await _refresh_token(session)

    return token.access_token


async def force_refresh_google_access_token(session) -> str:
    """Force refresh and return a new access token.

    This is used when Google API reports that the current token is invalid even
    if it hasn't expired yet.
    """

    token = await _refresh_token(session)
    return token.access_token


@dataclass
class GoogleAuthState:
    auth_status: str
    last_refresh: Optional[datetime]
    expires_in: Optional[int]
    failure_count: int
    last_error: Optional[str]


def get_google_auth_state(session) -> GoogleAuthState:
    token = get_token(session, "google")
    status = get_setting(session, SETTING_AUTH_STATUS) or AUTH_STATUS_NEEDS_REAUTH
    if status not in {AUTH_STATUS_OK, AUTH_STATUS_NEEDS_REAUTH}:
        status = AUTH_STATUS_NEEDS_REAUTH

    last_refresh_raw = get_setting(session, SETTING_LAST_REFRESH)
    last_refresh: Optional[datetime] = None
    if last_refresh_raw:
        try:
            last_refresh = datetime.fromisoformat(last_refresh_raw.replace("Z", "+00:00"))
        except ValueError:
            last_refresh = None

    expires_in: Optional[int] = None
    if token and token.expiry:
        delta = int((token.expiry - datetime.utcnow()).total_seconds())
        expires_in = delta if delta >= 0 else 0

    try:
        failures = int(get_setting(session, SETTING_REFRESH_FAILURES) or "0")
    except ValueError:
        failures = 0
    last_error = get_setting(session, SETTING_LAST_ERROR)

    if not token or not token.refresh_token:
        status = AUTH_STATUS_NEEDS_REAUTH

    return GoogleAuthState(
        auth_status=status,
        last_refresh=last_refresh,
        expires_in=expires_in,
        failure_count=failures,
        last_error=last_error,
    )


def google_auth_needs_reauth(session) -> bool:
    state = get_google_auth_state(session)
    return state.auth_status == AUTH_STATUS_NEEDS_REAUTH


def mark_google_auth_ready(session) -> None:
    _record_auth_ready(session)

