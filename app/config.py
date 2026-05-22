import os
from dataclasses import dataclass


def _env_bool(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default)
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")
    db_url: str = os.getenv("DB_URL", "sqlite:///./app.db")

    amo_base_url: str = os.getenv("AMO_BASE_URL", "https://example.amocrm.ru")
    amo_client_id: str = os.getenv("AMO_CLIENT_ID", "")
    amo_client_secret: str = os.getenv("AMO_CLIENT_SECRET", "")
    amo_redirect_uri: str = os.getenv("AMO_REDIRECT_URI", "http://localhost:8000/oauth/amocrm/callback")
    amo_long_lived_token: str = os.getenv("AMO_LONG_LIVED_TOKEN", "")

    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_scopes: str = os.getenv("GOOGLE_SCOPES", "https://www.googleapis.com/auth/contacts")
    google_redirect_uri: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/oauth/google/callback")
    auto_merge_duplicates: bool = _env_bool("AUTO_MERGE_DUPLICATES", "true")
    google_contact_group_name: str = os.getenv("GOOGLE_CONTACT_GROUP_NAME", "")

    webhook_shared_secret: str = os.getenv("WEBHOOK_SHARED_SECRET", "")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", os.getenv("WEBHOOK_SHARED_SECRET", ""))
    debug_secret: str = os.getenv("DEBUG_SECRET", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    alert_grace_seconds: int = int(os.getenv("ALERT_GRACE_SECONDS", "60"))
    telegram_alert_language: str = os.getenv("TELEGRAM_ALERT_LANGUAGE", "ru")
    service_display_name: str = os.getenv("SERVICE_DISPLAY_NAME", "AmoCRM Google Contacts Sync")
    render_service_dashboard_url: str = os.getenv("RENDER_SERVICE_DASHBOARD_URL", "")


settings = Settings()
