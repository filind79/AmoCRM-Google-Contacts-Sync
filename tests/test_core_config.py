from __future__ import annotations

import pytest

from app.core.config import get_settings, get_settings_snapshot


def _setenv(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _delenv(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_get_settings_long_lived_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(
        monkeypatch,
        AMO_AUTH_MODE="llt",
        AMO_LONG_LIVED_TOKEN=" token ",
        AMO_BASE_URL=" https://example.amocrm.ru ",
    )
    _delenv(monkeypatch, "AMO_API_KEY")
    settings = get_settings()
    assert settings["amo_auth_mode"] == "llt"
    assert settings["amo_has_llt"] is True
    assert settings["amo_has_api_key"] is False
    assert settings["amo_base_url"] == "https://example.amocrm.ru"


def test_get_settings_long_lived_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, AMO_AUTH_MODE="llt", AMO_LONG_LIVED_TOKEN="")
    with pytest.raises(RuntimeError, match="AmoCRM LLT missing"):
        get_settings()


def test_get_settings_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, AMO_AUTH_MODE="api_key", AMO_API_KEY=" key ")
    _delenv(monkeypatch, "AMO_LONG_LIVED_TOKEN")
    settings = get_settings()
    assert settings["amo_auth_mode"] == "api_key"
    assert settings["amo_has_api_key"] is True
    assert settings["amo_has_llt"] is False


def test_get_settings_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, AMO_AUTH_MODE="api_key")
    _delenv(monkeypatch, "AMO_API_KEY")
    with pytest.raises(RuntimeError, match="AmoCRM API key missing"):
        get_settings()


def test_get_settings_snapshot_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, AMO_AUTH_MODE="api_key")
    snapshot, error = get_settings_snapshot()
    assert snapshot["amo_auth_mode"] == "api_key"
    assert snapshot["amo_has_api_key"] is False
    assert error is not None
    assert "AmoCRM API key missing" in str(error)
