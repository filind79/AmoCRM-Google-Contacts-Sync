from fastapi import FastAPI

from app.auth import router as auth_router
from app.webhooks import router as webhook_router
from app.backfill import router as backfill_router
from app.debug import router as debug_router
from app.routes.sync import router as sync_router
from app.storage import init_db

app = FastAPI()


@app.on_event("startup")
def _startup() -> None:
    # Привязать engine и создать таблицы при первом запуске
    init_db()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(webhook_router)
app.include_router(backfill_router)
app.include_router(debug_router)
app.include_router(sync_router)
