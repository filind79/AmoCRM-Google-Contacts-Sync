from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.storage import get_setting, set_settings

AUTH_STATUS_OK = "ok"
AUTH_STATUS_NEEDS_REAUTH = "needs_reauth"

SETTING_AUTH_STATUS = "google_auth_status"
SETTING_FAILURE_COUNT = "google_auth_failure_count"
SETTING_LAST_REFRESH_AT = "google_last_refresh_at"
SETTING_LAST_FAILURE_AT = "google_last_failure_at"
SETTING_LAST_ERROR = "google_last_error"
SETTING_LAST_ALERT_SENT_AT = "google_last_alert_sent_at"
SETTING_LAST_RECOVERY_ALERT_SENT_AT = "google_last_recovery_alert_sent_at"


@dataclass
class GoogleIntegrationState:
    auth_status: str
    failure_count: int
    last_refresh_at: Optional[datetime]
    last_failure_at: Optional[datetime]
    last_error: Optional[str]
    last_alert_sent_at: Optional[datetime]
    last_recovery_alert_sent_at: Optional[datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_int(value: Optional[str], default: int = 0) -> int:
    try:
        return int(value or str(default))
    except ValueError:
        return default


def get_google_integration_state(session) -> GoogleIntegrationState:
    auth_status = get_setting(session, SETTING_AUTH_STATUS) or AUTH_STATUS_NEEDS_REAUTH
    if auth_status not in {AUTH_STATUS_OK, AUTH_STATUS_NEEDS_REAUTH}:
        auth_status = AUTH_STATUS_NEEDS_REAUTH
    return GoogleIntegrationState(
        auth_status=auth_status,
        failure_count=_parse_int(get_setting(session, SETTING_FAILURE_COUNT), 0),
        last_refresh_at=_parse_dt(get_setting(session, SETTING_LAST_REFRESH_AT)),
        last_failure_at=_parse_dt(get_setting(session, SETTING_LAST_FAILURE_AT)),
        last_error=get_setting(session, SETTING_LAST_ERROR),
        last_alert_sent_at=_parse_dt(get_setting(session, SETTING_LAST_ALERT_SENT_AT)),
        last_recovery_alert_sent_at=_parse_dt(get_setting(session, SETTING_LAST_RECOVERY_ALERT_SENT_AT)),
    )


def mark_google_auth_ok(session, now: Optional[datetime] = None) -> bool:
    current = get_google_integration_state(session)
    now = now or _utcnow()
    set_settings(
        session,
        {
            SETTING_AUTH_STATUS: AUTH_STATUS_OK,
            SETTING_FAILURE_COUNT: "0",
            SETTING_LAST_REFRESH_AT: _serialize_dt(now),
            SETTING_LAST_ERROR: None,
        },
    )
    return current.auth_status != AUTH_STATUS_OK


def mark_google_auth_failure(session, error: str, now: Optional[datetime] = None) -> tuple[bool, int]:
    current = get_google_integration_state(session)
    now = now or _utcnow()
    failures = max(0, current.failure_count) + 1
    set_settings(
        session,
        {
            SETTING_AUTH_STATUS: AUTH_STATUS_NEEDS_REAUTH,
            SETTING_FAILURE_COUNT: str(failures),
            SETTING_LAST_FAILURE_AT: _serialize_dt(now),
            SETTING_LAST_ERROR: error,
        },
    )
    changed = current.auth_status != AUTH_STATUS_NEEDS_REAUTH
    return changed, failures


def mark_google_alert_sent(session, now: Optional[datetime] = None) -> None:
    set_settings(session, {SETTING_LAST_ALERT_SENT_AT: _serialize_dt(now or _utcnow())})


def mark_google_recovery_alert_sent(session, now: Optional[datetime] = None) -> None:
    set_settings(session, {SETTING_LAST_RECOVERY_ALERT_SENT_AT: _serialize_dt(now or _utcnow())})
