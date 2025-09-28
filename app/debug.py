from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select

from app.config import settings
from app.core.config import get_settings_snapshot
from app.google_auth import GoogleAuthError, get_valid_google_access_token
from app.google_people import GOOGLE_API_BASE
from app.storage import Token, get_session, get_token
from app.webhooks import get_recent_webhook_events

router = APIRouter()

_ACCEPTED_WEBHOOK_AUTH = ("X-Webhook-Secret", "X-Debug-Secret", "?token")


def require_debug_secret(
    x_debug_secret: str | None = Header(None, alias="X-Debug-Secret"),
    token: str | None = Query(None),
) -> None:
    secret = settings.debug_secret
    provided = x_debug_secret or token
    if not secret or provided != secret:
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
    snapshot, error = get_settings_snapshot()
    auth_mode = snapshot.get("amo_auth_mode") or ""
    if auth_mode not in ("llt", "api_key"):
        auth_mode_display = auth_mode or "invalid"
        is_ready = False
    else:
        auth_mode_display = auth_mode
        if auth_mode == "llt":
            is_ready = bool(snapshot.get("amo_has_llt"))
        else:
            is_ready = bool(snapshot.get("amo_has_api_key"))
    payload: dict[str, object] = {
        "base_url": snapshot.get("amo_base_url"),
        "auth_mode": auth_mode_display,
        "is_ready": is_ready,
    }
    if error is not None:
        payload["error"] = str(error)
    return payload


@router.get("/config")
def debug_config(_=Depends(require_debug_secret)) -> dict[str, object]:
    snapshot, error = get_settings_snapshot()
    payload: dict[str, object] = {
        "amo": {
            "auth_mode": snapshot.get("amo_auth_mode"),
            "base_url": snapshot.get("amo_base_url"),
            "has_api_key": bool(snapshot.get("amo_has_api_key")),
            "has_llt": bool(snapshot.get("amo_has_llt")),
        }
    }
    if error is not None:
        payload["amo"]["validation_error"] = str(error)
    return payload


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
    scopes_list = sorted(token_scopes)
    token_expires_at = token.expiry.isoformat() if token and token.expiry else None
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
            response["error_reason"] = exc.reason
            response["scopes"] = scopes_list
            response["token_expires_at"] = token_expires_at
            response["can_read_connections"] = False
            response["can_write_contact"] = False
            return response
    finally:
        session.close()

    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"personFields": "metadata", "pageSize": 1}
    url = f"{GOOGLE_API_BASE}/people/me/connections"
    can_read_connections = False
    can_write_contact = False
    error_reason: str | None = None
    write_error: str | None = None
    write_status: int | None = None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers=headers, params=params)
            status_code = resp.status_code
            if status_code in (401, 403):
                scopes_ok = False
                error_reason = f"http_{status_code}"
            retry_after = _parse_retry_after(resp) if status_code == 429 else None

            if status_code != 200:
                latency_ms = _elapsed_ms(start)
                response = _base_response(False, latency_ms, status_code, scopes_ok)
                response["error"] = _extract_error(resp)
                if retry_after is not None:
                    response["retry_after"] = retry_after
                if error_reason:
                    response["error_reason"] = error_reason
                response["scopes"] = scopes_list
                response["token_expires_at"] = token_expires_at
                response["can_read_connections"] = False
                response["can_write_contact"] = False
                return response

            can_read_connections = True
            validate_headers = dict(headers)
            validate_headers["Content-Type"] = "application/json"
            validate_headers["X-Goog-Validate-Only"] = "true"
            validate_body = {"names": [{"givenName": "Ping"}]}
            write_resp = await client.post(
                f"{GOOGLE_API_BASE}/people:createContact",
                headers=validate_headers,
                json=validate_body,
            )
            write_status = write_resp.status_code
            if write_status == 200:
                can_write_contact = True
            else:
                can_write_contact = False
                write_error = _extract_error(write_resp)
                if write_status in (401, 403):
                    scopes_ok = False
                    error_reason = error_reason or f"http_{write_status}"
    except httpx.RequestError as exc:
        latency_ms = _elapsed_ms(start)
        response = _base_response(False, latency_ms, 503, scopes_ok)
        response["error"] = str(exc)
        if error_reason:
            response["error_reason"] = error_reason
        response["scopes"] = scopes_list
        response["token_expires_at"] = token_expires_at
        response["can_read_connections"] = can_read_connections
        response["can_write_contact"] = can_write_contact
        return response

    latency_ms = _elapsed_ms(start)
    status_value = write_status if write_status is not None else 200
    ok = can_read_connections and can_write_contact
    response = _base_response(ok, latency_ms, status_value, scopes_ok)
    response["scopes"] = scopes_list
    response["token_expires_at"] = token_expires_at
    response["can_read_connections"] = can_read_connections
    response["can_write_contact"] = can_write_contact
    if write_error:
        response["error"] = write_error
    if error_reason:
        response["error_reason"] = error_reason
    return response


@router.get("/webhook")
def debug_webhook(_=Depends(require_debug_secret)) -> dict[str, object]:
    return {
        "accepted_auth": list(_ACCEPTED_WEBHOOK_AUTH),
        "last_events": get_recent_webhook_events(),
    }
