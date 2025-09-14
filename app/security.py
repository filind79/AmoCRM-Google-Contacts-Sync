from __future__ import annotations

from hmac import compare_digest
from fastapi import Header, Query, HTTPException, status

from app.config import settings


def require_debug_secret(
    x_debug_secret: str | None = Header(None, alias="X-Debug-Secret"),
    key: str | None = Query(None),
) -> None:
    """Validate debug secret from header or query parameter."""
    secret = settings.debug_secret
    if not secret:
        raise HTTPException(status_code=500, detail="DEBUG_SECRET is not set")

    header_val = x_debug_secret or ""
    query_val = key or ""
    if compare_digest(header_val, secret) or compare_digest(query_val, secret):
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
