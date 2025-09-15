from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.google_auth import GoogleAuthError
from app.sync import dry_run_compare, fetch_amo_contacts, fetch_google_contacts

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
    except HTTPException as e:
        raise e
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

    analysis = dry_run_compare(amo_contacts, google_contacts, "both")
    summary = {
        "to_google": {
            "create": analysis["actions"]["amo_to_google"]["create"],
            "update": 0,
            "skip_existing": analysis["match"]["pairs"],
        },
        "to_amo": {
            "create": analysis["actions"]["google_to_amo"]["create"],
            "update": 0,
            "skip_existing": analysis["match"]["pairs"],
        },
    }
    samples = {
        "to_google_create": analysis["samples"]["amo_only"],
        "to_amo_create": analysis["samples"]["google_only"],
    }

    return {
        "status": "ok",
        "direction": direction,
        "google_sample": google_contacts[:5],
        "amo_sample": amo_contacts[:5],
        "counts": {"google": len(google_contacts), "amo": len(amo_contacts)},
        "summary": summary,
        "samples": samples,
    }
