import pytest
from fastapi import HTTPException
from unittest.mock import patch

from app.config import settings
from app.security import require_debug_secret


def test_header_valid(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "s")
    require_debug_secret("s", None)


def test_query_valid(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "s")
    require_debug_secret(None, "s")


def test_either_valid(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "s")
    require_debug_secret("wrong", "s")


def test_invalid(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "s")
    with pytest.raises(HTTPException) as exc:
        require_debug_secret(None, "wrong")
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException):
        require_debug_secret(None, None)


def test_compare_digest_used(monkeypatch):
    monkeypatch.setattr(settings, "debug_secret", "s")
    with patch("app.security.compare_digest", return_value=False) as mock_cd:
        with pytest.raises(HTTPException):
            require_debug_secret(None, None)
        assert mock_cd.call_count >= 2
