from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select

from app.config import settings
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
        return {"base_url": settings.amo_base_url, "has_token": bool(token)}
    finally:
        session.close()
