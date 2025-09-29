from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from app.amocrm import extract_name_and_fields, get_contact
from app.google_people import GoogleRateLimitError
from app.storage import (
    PendingSync,
    enqueue_pending_sync,
    fetch_due_pending_sync,
    get_session,
    save_link,
)
from app.services.sync_engine import SyncEngine


class PendingSyncWorker:
    def __init__(self, batch_size: int = 20) -> None:
        self.batch_size = batch_size
        self._task: asyncio.Task | None = None
        self._wake_event: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._stopping = False
        self._task = loop.create_task(self._run())
        logger.info("pending_sync.worker_started")

    async def stop(self) -> None:
        self._stopping = True
        if self._wake_event:
            self._wake_event.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._wake_event = None
        self._lock = None
        logger.info("pending_sync.worker_stopped")

    def wake(self) -> None:
        if self._wake_event:
            self._wake_event.set()

    async def drain(self, limit: Optional[int] = None) -> int:
        processed = await self._process_due(limit or self.batch_size)
        if processed:
            self.wake()
        return processed

    async def _run(self) -> None:
        try:
            while not self._stopping:
                processed = await self._process_due(self.batch_size)
                if processed:
                    await asyncio.sleep(0)
                    continue
                if self._stopping:
                    break
                if not self._wake_event:
                    await asyncio.sleep(1)
                    continue
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                finally:
                    if self._wake_event:
                        self._wake_event.clear()
        except asyncio.CancelledError:
            logger.warning("pending_sync.worker_cancelled")
            raise

    async def _process_due(self, limit: int) -> int:
        lock = self._lock or asyncio.Lock()
        if self._lock is None:
            self._lock = lock
        async with lock:
            session = get_session()
            try:
                records = fetch_due_pending_sync(session, limit)
                processed = 0
                for record in records:
                    await self._handle_record(session, record)
                    processed += 1
                return processed
            finally:
                session.close()

    async def _handle_record(self, session, record: PendingSync) -> None:
        contact_id = int(record.amo_contact_id)
        logger.debug("pending_sync.process", contact_id=contact_id, attempts=record.attempts)
        engine = SyncEngine()
        try:
            contact_data = await get_contact(contact_id)
            payload = extract_name_and_fields(contact_data)
            payload["id"] = contact_id
            plan = await engine.plan(payload)
            result = await engine.apply(plan)
        except GoogleRateLimitError as exc:
            delay = max(exc.retry_after or 0, self._retry_delay(record.attempts + 1))
            self._schedule_retry(session, record, delay, "google_rate_limit")
            logger.warning(
                "pending_sync.retry_rate_limit",
                contact_id=contact_id,
                delay=delay,
                attempts=record.attempts,
            )
        except RuntimeError as exc:
            message = str(exc)
            if "AmoCRM" in message and "missing" in message:
                self._fail_permanently(
                    session,
                    record,
                    reason="amo_auth_missing",
                    detail=message,
                )
                logger.error(
                    "pending_sync.dead_letter",
                    contact_id=contact_id,
                    reason="amo_auth_missing",
                    detail=message,
                )
                return
            raise
        except Exception as exc:  # pragma: no cover - defensive logging
            delay = self._retry_delay(record.attempts + 1)
            self._schedule_retry(session, record, delay, exc.__class__.__name__)
            logger.exception(
                "pending_sync.retry_error",
                contact_id=contact_id,
                attempts=record.attempts,
            )
        else:
            resource_name = result.resource_name if hasattr(result, "resource_name") else None
            if resource_name:
                save_link(session, str(contact_id), resource_name)
            session.delete(record)
            session.commit()
            logger.info(
                "pending_sync.synced",
                contact_id=contact_id,
                resource_name=resource_name,
                action=getattr(result, "action", None),
            )
        finally:
            engine.close()

    def _schedule_retry(self, session, record: PendingSync, delay_seconds: int, error: str) -> None:
        record.attempts += 1
        retry_delay = max(1, delay_seconds)
        record.next_attempt_at = datetime.utcnow() + timedelta(seconds=retry_delay)
        record.last_error = error
        record.updated_at = datetime.utcnow()
        session.commit()

    def _fail_permanently(
        self,
        session,
        record: PendingSync,
        *,
        reason: str,
        detail: str | None = None,
    ) -> None:
        record.attempts += 1
        record.next_attempt_at = datetime.utcnow() + timedelta(days=3650)
        error_text = reason
        if detail:
            error_text = f"{reason}:{detail}"
        record.last_error = error_text[:255]
        record.updated_at = datetime.utcnow()
        session.commit()

    @staticmethod
    def _retry_delay(attempt: int) -> int:
        base = 30
        cap = 1800
        delay = base * (2 ** max(0, attempt - 1))
        return min(cap, delay)


def enqueue_contact(contact_id: int) -> None:
    session = get_session()
    try:
        enqueue_pending_sync(session, contact_id)
    finally:
        session.close()


def get_worker() -> PendingSyncWorker:
    global pending_sync_worker
    return pending_sync_worker


pending_sync_worker = PendingSyncWorker()
