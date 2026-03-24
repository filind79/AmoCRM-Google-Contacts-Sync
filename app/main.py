import logging

from fastapi import FastAPI

from app.api.debug_merge import router as debug_merge_router
from app.auth import router as auth_router
from app.backfill import router as backfill_router
from app.config import settings
from app.core.config import get_settings_snapshot
from app.debug import router as debug_router
from app.google_auth import get_google_auth_state
from app.routes.sync import router as sync_router
from app.pending_sync_worker import pending_sync_worker
from app.storage import get_pending_sync_stats, get_session, init_db
from app.webhooks import router as webhook_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI()

    @app.on_event("startup")
    async def _startup() -> None:
        init_db()
        pending_sync_worker.start()
        amo_snapshot, amo_error = get_settings_snapshot()
        logger.info(
            "amo.config auth_mode=%s base_url=%s has_api_key=%s has_llt=%s valid=%s",
            amo_snapshot.get("amo_auth_mode") or "<unset>",
            amo_snapshot.get("amo_base_url") or "<unset>",
            bool(amo_snapshot.get("amo_has_api_key")),
            bool(amo_snapshot.get("amo_has_llt")),
            amo_error is None,
        )
        if amo_error:
            logger.error("amo.config_invalid %s", amo_error)
        if settings.debug_secret:
            logger.info("Debug router enabled on /debug")
        else:
            logger.info("Debug router mounted but inactive (no DEBUG_SECRET)")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await pending_sync_worker.stop()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    async def status() -> dict[str, object]:
        session = get_session()
        try:
            state = get_google_auth_state(session)
            queue_stats = get_pending_sync_stats(session)
            return {
                "google_auth_status": state.auth_status,
                "worker_status": pending_sync_worker.get_status(),
                "queue_status": queue_stats,
                "last_refresh_at": state.last_refresh_at.isoformat().replace("+00:00", "Z")
                if state.last_refresh_at
                else None,
                "failure_count": state.failure_count,
            }
        finally:
            session.close()

    app.include_router(auth_router)
    app.include_router(webhook_router)
    app.include_router(backfill_router)
    app.include_router(debug_router, prefix="/debug")
    app.include_router(debug_merge_router, prefix="/debug/merge")
    app.include_router(sync_router)
    return app


app = create_app()
