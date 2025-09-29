from __future__ import annotations

import asyncio
import time
from contextlib import suppress

from fastapi import APIRouter, Header, HTTPException, Query
from loguru import logger

from app.config import settings
from app.google_auth import GoogleAuthError
from app.google_people import GoogleRateLimitError, bind_metrics, reset_metrics
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


def _calculate_effective_limit(limit: int, direction: str, mode: str) -> tuple[int, bool]:
    if direction == "both" and mode == "fast" and limit > 20:
        return 20, True
    return limit, False


@router.get("/contacts/dry-run")
async def contacts_dry_run(
    limit: int = Query(50, ge=1, le=500),
    direction: str = Query("both"),
    since_days: int | None = Query(None, ge=1),
    since_minutes: int | None = Query(None, ge=1),
    mode: str = Query("fast"),
) -> dict[str, object]:
    direction = _validate_direction(direction)
    if mode not in {"fast", "full"}:
        raise HTTPException(status_code=400, detail="Invalid mode")

    effective_limit, limit_clamped = _calculate_effective_limit(limit, direction, mode)
    effective_since_days = None if since_minutes is not None else since_days

    metrics: dict[str, int] = {
        "google_requests": 0,
        "amo_requests": 0,
        "retries": 0,
        "rate_limit_hits": 0,
        "pages_google": 0,
        "pages_amo": 0,
    }
    token = bind_metrics(metrics)
    started = time.perf_counter()

    try:
        partial = False
        errors: list[dict[str, str]] = []
        counters: dict[str, int] = {}

        def _format_error_message(exc: Exception) -> str:
            if isinstance(exc, HTTPException):
                detail = exc.detail  # type: ignore[attr-defined]
                return str(detail)
            message = str(exc)
            return message if message else exc.__class__.__name__

        if direction == "both" and mode == "fast":
            amo_contacts: list[dict[str, object]] = []
            google_contacts: list[dict[str, object]] = []
            amo_task = asyncio.create_task(
                fetch_amo_contacts(
                    effective_limit,
                    effective_since_days,
                    since_minutes,
                    stats=metrics,
                )
            )
            google_task = asyncio.create_task(
                fetch_google_contacts(
                    effective_limit,
                    effective_since_days,
                    since_minutes,
                    None,
                    list_existing=True,
                    mode=mode,
                    stats=metrics,
                )
            )

            for task, side in ((amo_task, "amo"), (google_task, "google")):
                try:
                    if side == "amo":
                        amo_contacts = await asyncio.wait_for(task, timeout=20)
                    else:
                        google_contacts, counters = await asyncio.wait_for(task, timeout=20)
                except GoogleAuthError:
                    if side == "google":
                        if not amo_task.done():
                            amo_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await amo_task
                        from fastapi.responses import JSONResponse

                        return JSONResponse(
                            status_code=401,
                            content={
                                "detail": "Google auth required",
                                "auth_url": "/auth/google/start",
                            },
                        )
                    raise
                except asyncio.TimeoutError:
                    partial = True
                    errors.append(
                        {
                            "side": side,
                            "reason": "timeout",
                            "message": f"{side.capitalize()} fetch timed out",
                        }
                    )
                    if not task.done():
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                    if side == "google":
                        google_contacts = []
                        counters = {}
                    else:
                        amo_contacts = []
                except Exception as e:  # pragma: no cover - unexpected
                    partial = True
                    errors.append(
                        {
                            "side": side,
                            "reason": "fetch_error",
                            "message": _format_error_message(e),
                        }
                    )
                    if side == "google":
                        google_contacts = []
                        counters = {}
                    else:
                        amo_contacts = []
            google_contacts_result = google_contacts
        else:
            try:
                amo_contacts = (
                    await fetch_amo_contacts(
                        effective_limit,
                        effective_since_days,
                        since_minutes,
                        stats=metrics,
                    )
                    if direction in {"both", "amo"}
                    else []
                )
            except Exception as e:  # pragma: no cover - unexpected
                raise HTTPException(status_code=502, detail=f"AmoCRM API error: {e}")
            try:
                google_contacts, counters = await fetch_google_contacts(
                    effective_limit,
                    effective_since_days,
                    since_minutes,
                    amo_contacts if direction in {"both", "amo"} else None,
                    list_existing=direction in {"both", "google"},
                    mode=mode,
                    stats=metrics,
                )
            except GoogleAuthError:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Google auth required",
                        "auth_url": "/auth/google/start",
                    },
                )
            except Exception as e:  # pragma: no cover - unexpected
                raise HTTPException(status_code=502, detail=f"Google API error: {e}")
            google_contacts_result = google_contacts

        compare_direction = {
            "both": "both",
            "amo": "amo-to-google",
            "google": "google-to-amo",
        }[direction]
        compare = dry_run_compare(amo_contacts, google_contacts_result, compare_direction)

        actions: dict[str, object] = {}
        if direction in {"both", "amo"}:
            actions["amo_to_google"] = compare["actions"]["amo_to_google"]
        if direction in {"both", "google"}:
            actions["google_to_amo"] = compare["actions"]["google_to_amo"]

        samples: dict[str, object] = {"updates_preview": compare["samples"]["updates_preview"]}
        if direction in {"both", "amo"}:
            samples["amo_only"] = compare["samples"]["amo_only"]
            samples["skipped_invalid_phone"] = compare["samples"]["skipped_invalid_phone"]
        if direction in {"both", "google"}:
            samples["google_only"] = compare["samples"]["google_only"]

        duration_ms = int((time.perf_counter() - started) * 1000)

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
            "debug": {"counters": counters, "limit": effective_limit},
            "partial": partial,
            "errors": errors,
            "duration_ms": duration_ms,
            "google_requests": metrics.get("google_requests", 0),
            "amo_requests": metrics.get("amo_requests", 0),
            "retries": metrics.get("retries", 0),
            "rate_limit_hits": metrics.get("rate_limit_hits", 0),
            "pages_google": metrics.get("pages_google", 0),
            "pages_amo": metrics.get("pages_amo", 0),
            "limit_clamped": limit_clamped,
            "mode": mode,
        }
    finally:
        reset_metrics(token)


@router.post("/contacts/apply")
async def contacts_apply(
    limit: int = Query(5, ge=1, le=50),
    since_days: int | None = Query(None, ge=1),
    since_minutes: int | None = Query(None, ge=1),
    amo_ids: str | None = Query(None),
    direction: str = Query("to_google"),
    confirm: int | None = Query(None),
    x_debug_secret: str | None = Header(None, alias="X-Debug-Secret"),
    token: str | None = Query(None),
) -> dict[str, object]:
    provided_secret = x_debug_secret or token
    secret = settings.debug_secret
    if not secret or provided_secret != secret or confirm != 1:
        raise HTTPException(status_code=403)
    if direction != "to_google":
        raise HTTPException(status_code=400, detail="Invalid direction")
    parsed_amo_ids: list[int] | None = None
    if amo_ids:
        try:
            parsed_amo_ids = [int(part.strip()) for part in amo_ids.split(",") if part.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid amo_ids") from None
        if not parsed_amo_ids:
            parsed_amo_ids = None
    try:
        return await apply_contacts_to_google(
            limit,
            since_days,
            since_minutes,
            amo_ids=parsed_amo_ids,
        )
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
