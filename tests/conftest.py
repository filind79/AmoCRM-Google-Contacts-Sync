from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clear_amo_settings_cache():
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
