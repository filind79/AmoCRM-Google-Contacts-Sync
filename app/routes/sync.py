from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from app.config import settings
from app.google_auth import GoogleAuthError
from app.sync import apply_contacts_to_google, fetch_amo_contacts, fetch_google_contacts

router = APIRouter(prefix="/sync", tags=["sync"])


def _validate_direction(direction: str) -> str:
    allowed = {"both", "google", "amo"}
    if direction not in allowed:
        raise HTTPException(status_code=400, detail="Invalid direction")
    return direction


@router.get("/contacts/dry-run")
async def contacts_dry_run(
    limit: int = Query(50, ge=1, le=500),
    direction: str = Query("both"),
    since_days: int | None = Query(None, ge=1),
) -> dict[str, object]:
    direction = _validate_direction(direction)
    try:
        amo_contacts = await fetch_amo_contacts(limit) if direction in {"both", "amo"} else []
    except Exception as e:  # pragma: no cover - unexpected
        raise HTTPException(status_code=502, detail=f"AmoCRM API error: {e}")
    try:
        google_contacts = (
            await fetch_google_contacts(limit, since_days) if direction in {"both", "google"} else []
        )
    except GoogleAuthError:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=401,
            content={"detail": "Google auth required", "auth_url": "/auth/google/start"},
        )
    except Exception as e:  # pragma: no cover - unexpected
        raise HTTPException(status_code=502, detail=f"Google API error: {e}")

    return {
        "status": "ok",
        "direction": direction,
        "google_sample": google_contacts[:5],
        "amo_sample": amo_contacts[:5],
        "counts": {"google": len(google_contacts), "amo": len(amo_contacts)},
    }


@router.post("/contacts/apply")
async def contacts_apply(
    limit: int = Query(5, ge=1, le=50),
    since_days: int = Query(30, ge=1),
    direction: str = Query("to_google"),
    confirm: int | None = Query(None),
    x_debug_secret: str | None = Header(None, alias="X-Debug-Secret"),
) -> dict[str, object]:
    if x_debug_secret != settings.debug_secret or confirm != 1:
        raise HTTPException(status_code=403)
    if direction != "to_google":
        raise HTTPException(status_code=400, detail="Invalid direction")
    return await apply_contacts_to_google(limit, since_days)
