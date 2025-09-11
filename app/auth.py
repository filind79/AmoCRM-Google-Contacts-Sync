from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.config import settings
from app.storage import get_session, save_token

router = APIRouter()


def build_redirect(url: str, params: dict) -> RedirectResponse:
    return RedirectResponse(url=f"{url}?{urlencode(params)}")


@router.get("/auth/google/start")
async def auth_google_start():
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": settings.google_scopes,
        "access_type": "offline",
        "prompt": "consent",
    }
    return build_redirect("https://accounts.google.com/o/oauth2/v2/auth", params)


@router.get("/oauth/google/callback")
async def auth_google_callback(code: str):
    data = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data=data)
        resp.raise_for_status()
        token = resp.json()
    expires_in = token.get("expires_in")
    expiry = datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None
    session = get_session()
    save_token(
        session,
        "google",
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        expiry=expiry,
        scopes=settings.google_scopes,
    )
    return {"status": "ok"}


@router.get("/auth/amocrm/start")
async def auth_amocrm_start():
    params = {
        "client_id": settings.amo_client_id,
        "state": "",
        "mode": "post_message",
    }
    return build_redirect(f"{settings.amo_base_url}/oauth", params)


@router.get("/oauth/amocrm/callback")
async def auth_amocrm_callback(code: str):
    data = {
        "client_id": settings.amo_client_id,
        "client_secret": settings.amo_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.amo_redirect_uri,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{settings.amo_base_url}/oauth2/access_token", data=data)
        resp.raise_for_status()
        token = resp.json()
    expires_in = token.get("expires_in")
    expiry = datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None
    session = get_session()
    save_token(
        session,
        "amocrm",
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        expiry=expiry,
        scopes="",
        account_id=str(token.get("account_id")),
    )
    return {"status": "ok"}
