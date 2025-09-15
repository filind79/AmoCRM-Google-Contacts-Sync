from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from loguru import logger

from app.config import settings
from app.google_auth import GoogleAuthError
from app.google_people import GoogleRateLimitError
from app.sync import (
    apply_contacts_to_google,
    dry_run_compare,
    fetch_amo_contacts,
    fetch_google_contacts,
)

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
        google_contacts, counters = await fetch_google_contacts(
            limit,
            since_days,
            amo_contacts if direction in {"both", "amo"} else None,
            list_existing=direction in {"both", "google"},
        )
    except GoogleAuthError:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=401,
            content={"detail": "Google auth required", "auth_url": "/auth/google/start"},
        )
    except Exception as e:  # pragma: no cover - unexpected
        raise HTTPException(status_code=502, detail=f"Google API error: {e}")

    compare_direction = {
        "both": "both",
        "amo": "amo-to-google",
        "google": "google-to-amo",
    }[direction]
    compare = dry_run_compare(amo_contacts, google_contacts, compare_direction)

    actions: dict[str, object] = {}
    if direction in {"both", "amo"}:
        actions["amo_to_google"] = compare["actions"]["amo_to_google"]
    if direction in {"both", "google"}:
        actions["google_to_amo"] = compare["actions"]["google_to_amo"]

    samples: dict[str, object] = {"updates_preview": compare["samples"]["updates_preview"]}
    if direction in {"both", "amo"}:
        samples["amo_only"] = compare["samples"]["amo_only"]
    if direction in {"both", "google"}:
        samples["google_only"] = compare["samples"]["google_only"]

    return {
        "status": "ok",
        "direction": direction,
        "summary": {
            "amo": compare["amo"],
            "google": compare["google"],
            "match": compare["match"],
            "actions": actions,
        },
        "samples": samples,
        "debug": {"counters": counters},
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
    try:
        return await apply_contacts_to_google(limit, since_days)
    except GoogleRateLimitError as e:
        from fastapi.responses import JSONResponse

        content = e.payload
        content.setdefault("status", "rate_limited")
        content["rate_limit"] = {
            "retry_after_seconds": e.retry_after,
            "reason": "google_quota",
        }
        headers = {"Retry-After": str(e.retry_after)}
        return JSONResponse(status_code=429, content=content, headers=headers)
    except GoogleAuthError:
        logger.exception("sync.apply.failed")
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=401,
            content={"detail": "Google auth required", "auth_url": "/auth/google/start"},
        )
    except Exception as e:
        logger.exception("sync.apply.failed")
        raise HTTPException(status_code=502, detail=f"Apply failed: {e}")
