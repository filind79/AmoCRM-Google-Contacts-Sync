from __future__ import annotations

from typing import Any, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import select

from app.config import settings
from app.storage import Token, get_session, get_token

router = APIRouter(prefix="/debug", tags=["debug"])


def require_debug_key(
    x_debug_secret: str | None = Header(default=None, alias="X-Debug-Secret"),
    key: str | None = Query(None),
) -> None:
    secret = settings.debug_secret
    if not secret:
        raise HTTPException(status_code=500, detail="DEBUG_SECRET is not set")
    provided = x_debug_secret or key
    if provided != secret:
        raise HTTPException(status_code=403, detail="Forbidden")


async def _refresh_google_access_token(refresh_token: str) -> str:
    data = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data=data)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=resp.text)
    return resp.json().get("access_token", "")


async def _get_google_access_token() -> str:
    session = get_session()
    token = get_token(session, "google")
    session.close()
    if not token or not token.refresh_token:
        raise HTTPException(status_code=500, detail="Google token missing")
    return await _refresh_google_access_token(token.refresh_token)


@router.get("/google/ping")
async def google_ping(_=Depends(require_debug_key)) -> dict[str, Any]:
    access_token = await _get_google_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    url = "https://people.googleapis.com/v1/people/me"
    params = {"personFields": "names,emailAddresses"}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=resp.text)
    data = resp.json()
    names = data.get("names", [])
    emails = data.get("emailAddresses", [])
    name = names[0].get("displayName") if names else None
    email = emails[0].get("value") if emails else None
    return {"ok": True, "name": name, "email": email}


@router.get("/google/contacts")
async def google_contacts(limit: int = 10, _=Depends(require_debug_key)) -> dict[str, Any]:
    access_token = await _get_google_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    limit = max(1, min(limit, 50))
    params = {
        "pageSize": limit,
        "personFields": "names,emailAddresses",
        "sortOrder": "FIRST_NAME_ASCENDING",
    }
    url = "https://people.googleapis.com/v1/people/me/connections"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=resp.text)
    data = resp.json()
    items: List[dict[str, Any]] = []
    for person in data.get("connections", []):
        names = person.get("names", [])
        emails = person.get("emailAddresses", [])
        items.append(
            {
                "resourceName": person.get("resourceName"),
                "name": names[0].get("displayName") if names else None,
                "email": emails[0].get("value") if emails else None,
            }
        )
    return {"ok": True, "count": len(items), "items": items}


@router.get("/amo/ping")
async def amo_ping(_=Depends(require_debug_key)) -> dict[str, Any]:
    base_url = settings.amo_base_url.rstrip("/")
    token = settings.amo_long_lived_token
    if not base_url or not token:
        raise HTTPException(status_code=500, detail="AMO_BASE_URL or AMO_LONG_LIVED_TOKEN is not set")
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{base_url}/api/v4/account"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=resp.text)
    data = resp.json()
    return {
        "ok": True,
        "id": data.get("id"),
        "name": data.get("name"),
        "subdomain": data.get("subdomain"),
    }


@router.get("/db/token")
async def db_token(_=Depends(require_debug_key)) -> dict[str, Any]:
    session = get_session()
    tokens = session.execute(select(Token)).scalars().all()
    session.close()
    items = [
        {
            "system": t.system,
            "has_refresh": bool(t.refresh_token),
            "expires_at": t.expiry.isoformat() if t.expiry else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in tokens
    ]
    return {"ok": True, "tokens": items}
