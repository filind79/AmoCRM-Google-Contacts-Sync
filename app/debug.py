from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import settings
from app.google_auth import GoogleAuthError
from app.google_people import GOOGLE_API_BASE, get_access_token
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


@router.get("/ping-google")
async def ping_google(_=Depends(require_debug_secret)):
    start = time.perf_counter()
    try:
        token = await get_access_token()
    except GoogleAuthError:
        return JSONResponse(
            status_code=401,
            content={"detail": "Google auth required", "auth_url": "/auth/google/start"},
        )

    headers = {"Authorization": f"Bearer {token}"}
    params = {"personFields": "names"}
    url = f"{GOOGLE_API_BASE}/people/me"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.RequestError as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"ok": False, "latency_ms": latency_ms, "retry_after": None, "error": str(exc)}

    latency_ms = int((time.perf_counter() - start) * 1000)

    if resp.status_code == 401:
        return JSONResponse(
            status_code=401,
            content={"detail": "Google auth required", "auth_url": "/auth/google/start"},
        )

    if resp.status_code == 429:
        retry_after = _parse_retry_after(resp)
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "retry_after": retry_after,
            "error": "rate_limited",
        }

    if resp.status_code != 200:
        detail = None
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "retry_after": None,
            "error": detail,
            "status": resp.status_code,
        }

    return {"ok": True, "latency_ms": latency_ms, "retry_after": None}
