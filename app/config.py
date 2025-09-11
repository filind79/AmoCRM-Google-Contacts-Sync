import os
from dataclasses import dataclass


@dataclass
class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")
    db_url: str = os.getenv("DB_URL", "sqlite:///./app.db")

    amo_base_url: str = os.getenv("AMO_BASE_URL", "https://example.amocrm.ru")
    amo_client_id: str = os.getenv("AMO_CLIENT_ID", "")
    amo_client_secret: str = os.getenv("AMO_CLIENT_SECRET", "")
    amo_redirect_uri: str = os.getenv("AMO_REDIRECT_URI", "http://localhost:8000/oauth/amocrm/callback")

    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_scopes: str = os.getenv("GOOGLE_SCOPES", "https://www.googleapis.com/auth/contacts")
    google_redirect_uri: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/oauth/google/callback")

    webhook_shared_secret: str = os.getenv("WEBHOOK_SHARED_SECRET", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
