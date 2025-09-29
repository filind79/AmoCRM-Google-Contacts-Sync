from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.pending_sync_worker import PendingSyncWorker
from app.storage import PendingSync, enqueue_pending_sync, get_session, init_db


@pytest.mark.asyncio
async def test_worker_dead_letters_on_missing_amo_token(monkeypatch):
    init_db()
    session = get_session()
    try:
        record = enqueue_pending_sync(session, 123)
        record_id = record.id
    finally:
        session.close()

    async def missing_contact(contact_id: int):  # noqa: D401
        raise RuntimeError("AmoCRM API key missing")

    monkeypatch.setattr("app.pending_sync_worker.get_contact", missing_contact)

    worker = PendingSyncWorker()
    session = get_session()
    try:
        record = session.get(PendingSync, record_id)
        assert record is not None
        await worker._handle_record(session, record)
        session.refresh(record)
        stored_error = record.last_error
        stored_attempts = record.attempts
        next_attempt_at = record.next_attempt_at
    finally:
        session.close()

    assert stored_error.startswith("amo_auth_missing")
    assert stored_attempts == 1
    assert next_attempt_at > datetime.utcnow() + timedelta(days=3000)
