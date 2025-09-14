from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.debug import require_debug_key
from app.sync import fetch_amo_contacts, fetch_google_contacts, dry_run_compare

router = APIRouter(prefix="/sync", tags=["sync"])


def _validate_direction(direction: str) -> str:
    allowed = {"both", "amo-to-google", "google-to-amo"}
    if direction not in allowed:
        raise HTTPException(status_code=400, detail="Invalid direction")
    return direction


@router.get("/contacts/dry-run")
async def contacts_dry_run(
    limit: int = Query(50, ge=1, le=200),
    direction: str = Query("both"),
    _=Depends(require_debug_key),
) -> dict[str, object]:
    direction = _validate_direction(direction)
    try:
        amo_contacts = await fetch_amo_contacts(limit)
    except HTTPException as e:
        raise e
    except Exception as e:  # pragma: no cover - unexpected
        raise HTTPException(status_code=502, detail=f"AmoCRM API error: {e}")
    try:
        google_contacts = await fetch_google_contacts(limit)
    except HTTPException as e:
        raise e
    except Exception as e:  # pragma: no cover - unexpected
        raise HTTPException(status_code=502, detail=f"Google API error: {e}")
    summary = dry_run_compare(amo_contacts, google_contacts, direction)
    return {"input": {"limit": limit, "direction": direction}, **summary}
