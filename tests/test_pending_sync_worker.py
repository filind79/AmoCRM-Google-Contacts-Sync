from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.alerts import AlertCategory
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


@pytest.mark.asyncio
async def test_supervisor_recovery_uses_sent_problem_category(monkeypatch):
    init_db()
    worker = PendingSyncWorker()
    worker._task = SimpleNamespace(done=lambda: False)
    worker._stopping = False
    worker._processing_stall_seconds = 1
    worker.processing_last_heartbeat_at = datetime.utcnow() - timedelta(seconds=200)
    worker.processing_last_success_at = datetime.utcnow() - timedelta(seconds=200)

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr("app.pending_sync_worker.get_session", lambda: DummySession())
    monkeypatch.setattr(
        "app.pending_sync_worker.get_pending_sync_health_stats",
        lambda _session: {"queue_pending_count": 3},
    )
    monkeypatch.setattr("app.pending_sync_worker.settings.alert_grace_seconds", 0)
    async def fake_restart(self):
        return None

    monkeypatch.setattr("app.pending_sync_worker.PendingSyncWorker._restart_processing_loop", fake_restart)

    problem_categories = []
    recovery_categories = []

    def fake_problem(category, technical=None):
        problem_categories.append(category)
        return True

    def fake_recovery(category, technical=None):
        recovery_categories.append(category)
        return True

    monkeypatch.setattr("app.pending_sync_worker.send_problem_alert", fake_problem)
    monkeypatch.setattr("app.pending_sync_worker.send_recovery_alert", fake_recovery)

    await worker._supervisor_check()
    monkeypatch.setattr(
        "app.pending_sync_worker.get_pending_sync_health_stats",
        lambda _session: {"queue_pending_count": 0},
    )
    worker.processing_last_heartbeat_at = datetime.utcnow()
    worker.processing_last_success_at = datetime.utcnow()
    await worker._supervisor_check()

    assert problem_categories == [AlertCategory.QUEUE_BACKLOG_UNRECOVERED]
    assert recovery_categories == [AlertCategory.QUEUE_BACKLOG_UNRECOVERED]
