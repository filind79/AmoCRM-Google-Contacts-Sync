"""Configuration helpers for AmoCRM integration."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, Tuple


def _norm(value: str | None) -> str:
    """Normalize environment variables by trimming whitespace."""

    return (value or "").strip()


def _load_settings() -> Dict[str, Any]:
    """Load AmoCRM-related settings from environment variables."""

    mode = _norm(os.getenv("AMO_AUTH_MODE")).lower()
    base_url = _norm(os.getenv("AMO_BASE_URL")) or "https://example.amocrm.ru"
    api_key = _norm(os.getenv("AMO_API_KEY"))
    llt = _norm(os.getenv("AMO_LONG_LIVED_TOKEN"))
    return {
        "amo_auth_mode": mode,
        "amo_base_url": base_url,
        "amo_has_api_key": bool(api_key),
        "amo_has_llt": bool(llt),
    }


def _validate(settings: Dict[str, Any]) -> None:
    """Validate the AmoCRM configuration."""

    mode = settings.get("amo_auth_mode")
    if mode not in ("llt", "api_key"):
        raise RuntimeError(f"Invalid AMO_AUTH_MODE: {mode or '<empty>'}")
    if mode == "llt" and not settings.get("amo_has_llt"):
        raise RuntimeError("AmoCRM LLT missing")
    if mode == "api_key" and not settings.get("amo_has_api_key"):
        raise RuntimeError("AmoCRM API key missing")


@lru_cache(maxsize=1)
def _get_settings_cached() -> Dict[str, Any]:
    settings = _load_settings()
    _validate(settings)
    return settings


def get_settings(*, validate: bool = True) -> Dict[str, Any]:
    """Return AmoCRM configuration.

    Parameters
    ----------
    validate:
        When ``True`` (default) the settings are validated and cached. When
        ``False`` the raw values are returned without validation or caching.
    """

    if validate:
        return _get_settings_cached()
    return _load_settings()


def get_settings_snapshot() -> Tuple[Dict[str, Any], RuntimeError | None]:
    """Return current settings alongside a validation error, if any."""

    settings = _load_settings()
    try:
        _validate(settings)
    except RuntimeError as exc:
        return settings, exc
    return settings, None


def clear_settings_cache() -> None:
    """Clear the cached AmoCRM settings."""

    _get_settings_cached.cache_clear()


# Expose ``cache_clear`` to ease testing (pytest expects attribute on function).
get_settings.cache_clear = clear_settings_cache  # type: ignore[attr-defined]
