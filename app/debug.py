from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text

from app.config import settings
from app.storage import get_engine, get_session, get_token

router = APIRouter()


def require_debug_secret(x_debug_secret: str | None = Header(None, alias="X-Debug-Secret")) -> None:
    secret = settings.debug_secret
    if not secret or x_debug_secret != secret:
        raise HTTPException(status_code=401, detail="invalid debug secret")


@router.get("/ping")
def debug_ping(_=Depends(require_debug_secret)) -> dict[str, str]:
    return {"status": "ok"}


@router.get("/db")
def debug_db(_=Depends(require_debug_secret)) -> dict[str, object]:
    engine = get_engine()
    ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        ok = False
    return {"dialect": engine.dialect.name, "ok": ok}


@router.get("/google")
def debug_google(_=Depends(require_debug_secret)) -> dict[str, bool]:
    session = get_session()
    try:
        token = get_token(session, "google")
        return {"has_token": bool(token)}
    finally:
        session.close()


@router.get("/amo")
def debug_amo(_=Depends(require_debug_secret)) -> dict[str, bool]:
    session = get_session()
    try:
        token = get_token(session, "amocrm")
        return {
            "has_token": bool(token),
            "has_base_url": bool(settings.amo_base_url),
        }
    finally:
        session.close()
