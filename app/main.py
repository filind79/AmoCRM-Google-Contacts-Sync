import logging

from fastapi import FastAPI

from app.auth import router as auth_router
from app.backfill import router as backfill_router
from app.config import settings
from app.debug import router as debug_router
from app.routes.sync import router as sync_router
from app.storage import init_db
from app.webhooks import router as webhook_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI()

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        if settings.debug_secret:
            logger.info("Debug router enabled on /debug")
        else:
            logger.info("Debug router mounted but inactive (no DEBUG_SECRET)")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(webhook_router)
    app.include_router(backfill_router)
    app.include_router(debug_router, prefix="/debug")
    app.include_router(sync_router)
    return app


app = create_app()
