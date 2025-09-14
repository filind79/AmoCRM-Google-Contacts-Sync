from fastapi import FastAPI

from app.auth import router as auth_router
from app.webhooks import router as webhook_router
from app.backfill import router as backfill_router
from app.storage import init_db

app = FastAPI()


@app.on_event("startup")
def _startup():
    # создаём таблицы, если их нет (алембик катается в Docker CMD)
    init_db()

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(webhook_router)
app.include_router(backfill_router)
