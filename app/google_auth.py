"""Utilities for storing and refreshing Google OAuth tokens.

This module provides a small abstraction around the token storage in the
database.  The public function :func:`get_valid_google_access_token` returns a
usable access token, refreshing it with Google if it is about to expire.  When
refreshing fails or is impossible a :class:`GoogleAuthError` is raised so that
callers can react appropriately (e.g. by asking the user to re-authorise).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.config import settings
from app.storage import Token, get_token, save_token


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class GoogleAuthError(Exception):
    """Raised when Google authentication fails or requires user interaction."""

    reason: str
    auth_url: Optional[str] = None


async def _refresh_token(session) -> Token:
    """Refresh the Google OAuth token stored in the database."""

    token = get_token(session, "google")
    if not token or not token.refresh_token:
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
        raise GoogleAuthError(f"network_error: {exc}")

    if resp.status_code != 200:
        raise GoogleAuthError("refresh_failed", "/auth/google/start")

    payload = resp.json()
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)
    new_refresh = payload.get("refresh_token") or token.refresh_token

    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    save_token(
        session,
        "google",
        access_token=access_token,
        refresh_token=new_refresh,
        expiry=expiry.replace(tzinfo=None),
        scopes=token.scopes or "",
        account_id=token.account_id,
    )
    return get_token(session, "google")


async def get_valid_google_access_token(session) -> str:
    """Return a valid access token for Google APIs.

    If the current token is close to expiring (within 60 seconds) it is
    refreshed automatically.  :class:`GoogleAuthError` is raised when the token
    is missing or cannot be refreshed.
    """

    token = get_token(session, "google")
    if not token:
        raise GoogleAuthError("token_missing", "/auth/google/start")

    now = datetime.utcnow()
    if not token.expiry or token.expiry <= now + timedelta(seconds=60):
        token = await _refresh_token(session)

    return token.access_token


async def force_refresh_google_access_token(session) -> str:
    """Force refresh and return a new access token.

    This is used when Google API reports that the current token is invalid even
    if it hasn't expired yet.
    """

    token = await _refresh_token(session)
    return token.access_token

