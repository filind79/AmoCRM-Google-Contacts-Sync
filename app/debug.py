from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select

from app.config import settings
from app.google_auth import GoogleAuthError, get_valid_google_access_token
from app.google_people import GOOGLE_API_BASE
from app.storage import Token, get_session, get_token

router = APIRouter()


def require_debug_secret(x_debug_secret: str | None = Header(None, alias="X-Debug-Secret")) -> None:
    secret = settings.debug_secret
    if not secret or x_debug_secret != secret:
        raise HTTPException(status_code=404)


@router.get("/db")
def debug_db(_=Depends(require_debug_secret)) -> dict[str, object]:
    session = get_session()
    try:
        session.execute(select(1))
        tokens = session.execute(select(Token)).scalars().all()
        return {"db": "ok", "tokens": len(tokens)}
    finally:
        session.close()


@router.get("/google")
def debug_google(_=Depends(require_debug_secret)) -> dict[str, object]:
    session = get_session()
    try:
        token = get_token(session, "google")
        if not token:
            return {"has_token": False, "expires_at": None, "scopes": None}
        expires = token.expiry.isoformat() if token.expiry else None
        return {"has_token": True, "expires_at": expires, "scopes": token.scopes}
    finally:
        session.close()


@router.get("/amo")
def debug_amo(_=Depends(require_debug_secret)) -> dict[str, object]:
    session = get_session()
    try:
        token = get_token(session, "amocrm")
        auth_mode = "none"
        is_ready = False
        if token and token.access_token:
            auth_mode = "oauth"
            if not token.expiry:
                is_ready = True
            else:
                now = datetime.utcnow()
                is_ready = token.expiry > now
        elif settings.amo_long_lived_token:
            auth_mode = "api_key"
            is_ready = True
        return {
            "base_url": settings.amo_base_url,
            "auth_mode": auth_mode,
            "is_ready": is_ready,
        }
    finally:
        session.close()


def _parse_retry_after(resp: httpx.Response) -> int | None:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return int(float(header))
        except ValueError:
            parsed = parsedate_to_datetime(header)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                delta = parsed - datetime.now(timezone.utc)
                return int(delta.total_seconds()) if delta.total_seconds() > 0 else 0
    reset_header = resp.headers.get("X-RateLimit-Reset")
    if reset_header:
        try:
            reset_ts = float(reset_header)
            wait = int(reset_ts - time.time())
            return wait if wait > 0 else 0
        except ValueError:
            return None
    return None


def _elapsed_ms(start: float) -> int:
    elapsed = int((time.perf_counter() - start) * 1000)
    return elapsed if elapsed > 0 else 1


def _scope_set(scopes: str | None) -> set[str]:
    if not scopes:
        return set()
    return {scope for scope in scopes.replace(",", " ").split() if scope}


def _extract_error(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        text = resp.text.strip()
        return text or f"HTTP {resp.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
            status = error.get("status")
            if isinstance(status, str) and status:
                return status
        if isinstance(error, str) and error:
            return error
    return str(payload)


_AUTH_ERROR_STATUS: dict[str, int] = {
    "token_missing": 401,
    "refresh_unavailable": 401,
    "refresh_failed": 403,
}


def _base_response(ok: bool, latency_ms: int, status: int | None, scopes_ok: bool) -> dict[str, object]:
    return {
        "ok": ok,
        "latency_ms": latency_ms,
        "status": status,
        "scopes_ok": scopes_ok,
    }


@router.get("/ping-google")
async def ping_google(_=Depends(require_debug_secret)) -> dict[str, object]:
    start = time.perf_counter()
    required_scopes = _scope_set(settings.google_scopes)
    session = get_session()
    access_token: str | None = None
    token = get_token(session, "google")
    token_scopes = _scope_set(token.scopes if token else None)
    if required_scopes:
        scopes_ok = bool(token) and required_scopes.issubset(token_scopes)
    else:
        scopes_ok = bool(token)
    try:
        try:
            access_token = await get_valid_google_access_token(session)
        except GoogleAuthError as exc:
            latency_ms = _elapsed_ms(start)
            status = _AUTH_ERROR_STATUS.get(exc.reason, 503)
            response = _base_response(False, latency_ms, status, scopes_ok)
            response["error"] = exc.reason
            return response
    finally:
        session.close()

    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"personFields": "metadata"}
    url = f"{GOOGLE_API_BASE}/people/me"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.RequestError as exc:
        latency_ms = _elapsed_ms(start)
        response = _base_response(False, latency_ms, 503, scopes_ok)
        response["error"] = str(exc)
        return response

    latency_ms = _elapsed_ms(start)
    status_code = resp.status_code
    if status_code in (401, 403):
        scopes_ok = False
    retry_after = _parse_retry_after(resp) if status_code == 429 else None

    if status_code != 200:
        response = _base_response(False, latency_ms, status_code, scopes_ok)
        response["error"] = _extract_error(resp)
        if retry_after is not None:
            response["retry_after"] = retry_after
        return response

    return _base_response(True, latency_ms, status_code, scopes_ok)
