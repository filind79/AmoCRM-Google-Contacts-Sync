from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy.exc import OperationalError

from app.amocrm import extract_name_and_fields, get_contact
from app.alerts import AlertCategory, send_problem_alert, send_recovery_alert
from app.config import settings
from app.google_auth import (
    GoogleAuthError,
    get_valid_google_access_token,
    google_auth_needs_reauth,
)
from app.google_people import GoogleRateLimitError
from app.storage import (
    PendingSync,
    enqueue_pending_sync,
    fetch_due_pending_sync,
    get_pending_sync_health_stats,
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
        self._refresh_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._auth_blocked_logged = False
        self.processing_loop_running = False
        self.refresh_loop_running = False
        self.processing_last_heartbeat_at: datetime | None = None
        self.processing_last_success_at: datetime | None = None
        self.refresh_last_heartbeat_at: datetime | None = None
        self.refresh_last_success_at: datetime | None = None
        self.processing_last_started_at: datetime | None = None
        self.recovery_in_progress = False
        self.backlog_detected = False
        self._backlog_alert_sent = False
        self._problem_detected_at: datetime | None = None
        self._processing_alert_sent = False
        self._last_problem_alert_category: AlertCategory | None = None
        self._processing_loop_restart_count = 0
        self._processing_stall_seconds = 150
        self._processing_backlog_stall_seconds = 90
        self._supervisor_interval_seconds = 30

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._stopping = False
        self._auth_blocked_logged = False
        self._task = loop.create_task(self._run())
        self._refresh_task = loop.create_task(self._refresh_loop())
        self._supervisor_task = loop.create_task(self._supervisor_loop())
        logger.info("pending_sync.worker_started")

    async def stop(self) -> None:
        self._stopping = True
        if self._wake_event:
            self._wake_event.set()
        if self._refresh_task:
            self._refresh_task.cancel()
        if self._supervisor_task:
            self._supervisor_task.cancel()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._wake_event = None
        self._lock = None
        if self._refresh_task:
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
        if self._supervisor_task:
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
            self._supervisor_task = None
        logger.info("pending_sync.worker_stopped")

    def wake(self) -> None:
        if self._wake_event:
            self._wake_event.set()

    async def drain(self, limit: Optional[int] = None) -> int:
        if self._stopping and not self._is_background_running():
            self._stopping = False
        processed = await self._process_due(
            limit or self.batch_size,
            enforce_auth_block=self._is_background_running(),
        )
        if processed:
            self.wake()
        return processed

    async def _run(self) -> None:
        self.processing_loop_running = True
        self.processing_last_started_at = datetime.utcnow()
        self._heartbeat_processing("started")
        logger.info("pending_sync.processing_loop_started")
        try:
            while not self._stopping:
                self._heartbeat_processing("before_process")
                if self._stopping:
                    break
                processed = await self._process_due(self.batch_size)
                self._heartbeat_processing("after_process_due")
                if processed:
                    self.processing_last_success_at = datetime.utcnow()
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
                    self._heartbeat_processing("idle")
                    if self._wake_event:
                        self._wake_event.clear()
        except asyncio.CancelledError:
            logger.warning("pending_sync.worker_cancelled")
            raise
        except Exception:
            logger.exception("pending_sync.processing_loop_failed")
            raise
        finally:
            self.processing_loop_running = False
            logger.info("pending_sync.processing_loop_stopped")

    def _is_background_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def _process_due(self, limit: int, *, enforce_auth_block: bool = True) -> int:
        lock = self._lock or asyncio.Lock()
        if self._lock is None:
            self._lock = lock
        async with lock:
            if self._stopping and enforce_auth_block:
                return 0
            session = get_session()
            try:
                if enforce_auth_block and not self._stopping and google_auth_needs_reauth(session):
                    if not self._auth_blocked_logged:
                        logger.warning("Google authorization required, sync postponed")
                        self._auth_blocked_logged = True
                    return 0
                self._auth_blocked_logged = False
                if self._stopping and enforce_auth_block:
                    return 0
                records = fetch_due_pending_sync(session, limit)
                processed = 0
                for record in records:
                    if self._stopping and enforce_auth_block:
                        break
                    await self._handle_record(session, record)
                    processed += 1
                return processed
            except OperationalError:
                if self._stopping:
                    logger.warning("shutdown.db_error_suppressed")
                    return 0
                raise
            finally:
                session.close()

    async def _refresh_loop(self) -> None:
        interval = 60 * 60
        self.refresh_loop_running = True
        try:
            while not self._stopping:
                self.refresh_last_heartbeat_at = datetime.utcnow()
                session = get_session()
                try:
                    try:
                        await get_valid_google_access_token(session, force_refresh=True)
                    except GoogleAuthError as exc:
                        logger.warning(
                            "google_auth.refresh_failed reason=%s",
                            exc.reason,
                        )
                    else:
                        logger.info("google_auth.refresh_ok")
                        self.refresh_last_success_at = datetime.utcnow()
                finally:
                    session.close()
                for _ in range(interval // 60):
                    if self._stopping:
                        break
                    await asyncio.sleep(60)
                else:
                    remainder = interval % 60
                    if remainder:
                        await asyncio.sleep(remainder)
                if self._stopping:
                    break
        except asyncio.CancelledError:
            logger.debug("pending_sync.refresh_cancelled")
            raise
        finally:
            self.refresh_loop_running = False

    async def _supervisor_loop(self) -> None:
        while not self._stopping:
            try:
                await self._supervisor_check()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("pending_sync.supervisor_check_failed")
            await asyncio.sleep(self._supervisor_interval_seconds)

    async def _supervisor_check(self) -> None:
        if self._stopping:
            return
        session = get_session()
        try:
            stats = get_pending_sync_health_stats(session)
            pending_count = int(stats["queue_pending_count"])
        finally:
            session.close()
        now = datetime.utcnow()
        heartbeat_age = (
            (now - self.processing_last_heartbeat_at).total_seconds()
            if self.processing_last_heartbeat_at
            else float("inf")
        )
        success_age = (
            (now - self.processing_last_success_at).total_seconds()
            if self.processing_last_success_at
            else float("inf")
        )
        dead_task = bool(self._task and self._task.done() and not self._stopping)
        stalled = heartbeat_age > self._processing_stall_seconds
        backlog_stalled = pending_count > 0 and success_age > self._processing_backlog_stall_seconds
        if pending_count > 0 and not self.backlog_detected:
            self.backlog_detected = True
            logger.warning("pending_sync.queue_backlog_detected")
        if pending_count == 0 and self.backlog_detected:
            self.backlog_detected = False
            self._backlog_alert_sent = False
            logger.info("pending_sync.queue_backlog_cleared")
        if dead_task or stalled or backlog_stalled:
            logger.error(
                "pending_sync.processing_loop_stalled dead_task=%s stalled=%s backlog_stalled=%s heartbeat_age=%.1f success_age=%.1f pending_count=%s",
                dead_task,
                stalled,
                backlog_stalled,
                heartbeat_age,
                success_age,
                pending_count,
            )
            if self._problem_detected_at is None:
                self._problem_detected_at = now
            elapsed = (now - self._problem_detected_at).total_seconds()
            if elapsed >= settings.alert_grace_seconds and not self._processing_alert_sent:
                category = (
                    AlertCategory.QUEUE_BACKLOG_UNRECOVERED
                    if pending_count > 0
                    else AlertCategory.PROCESSING_LOOP_STALLED_UNRECOVERED
                )
                sent = send_problem_alert(category, technical=f"pending_count={pending_count} heartbeat_age={heartbeat_age:.1f}")
                if sent:
                    self._processing_alert_sent = True
                    self._backlog_alert_sent = True
                    self._last_problem_alert_category = category
            await self._restart_processing_loop()
        elif self._problem_detected_at is not None:
            elapsed = (now - self._problem_detected_at).total_seconds()
            if self._processing_alert_sent and self._last_problem_alert_category is not None:
                send_recovery_alert(self._last_problem_alert_category, technical=f"pending_count={pending_count}")
            else:
                logger.info("telegram_alert.suppressed_auto_recovered elapsed=%.1f", elapsed)
                logger.info("telegram_alert.recovery_skipped_no_initial_alert category=processing_loop_stalled_unrecovered")
            self._problem_detected_at = None
            self._processing_alert_sent = False
            self._last_problem_alert_category = None

    async def _restart_processing_loop(self) -> None:
        if self.recovery_in_progress or self._stopping:
            return
        self.recovery_in_progress = True
        try:
            if self._task and not self._task.done():
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._run())
            self._processing_loop_restart_count += 1
            logger.warning("pending_sync.processing_loop_restarted count=%s", self._processing_loop_restart_count)
        finally:
            self.recovery_in_progress = False

    def _heartbeat_processing(self, reason: str) -> None:
        self.processing_last_heartbeat_at = datetime.utcnow()
        logger.debug("pending_sync.processing_loop_heartbeat reason=%s", reason)

    async def _handle_record(self, session, record: PendingSync) -> None:
        contact_id = int(record.amo_contact_id)
        start = time.perf_counter()
        logger.info(
            "pending_sync.process",
            record_id=record.id,
            contact_id=contact_id,
            attempt=record.attempts + 1,
        )
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
                record_id=record.id,
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
                    record_id=record.id,
                    contact_id=contact_id,
                    reason="amo_auth_missing",
                    detail=message,
                )
                return
            raise
        except Exception as exc:  # pragma: no cover - defensive logging
            delay = self._retry_delay(record.attempts + 1)
            self._schedule_retry(session, record, delay, exc.__class__.__name__)
            logger.warning(
                "pending_sync.retry_error",
                record_id=record.id,
                contact_id=contact_id,
                attempts=record.attempts,
                error_class=exc.__class__.__name__,
                reason=str(exc)[:200],
            )
        else:
            resource_name = result.resource_name if hasattr(result, "resource_name") else None
            action = getattr(result, "action", None)
            if resource_name:
                save_link(session, str(contact_id), resource_name)
            session.delete(record)
            session.commit()
            logger.info(
                "pending_sync.synced",
                record_id=record.id,
                contact_id=contact_id,
                resource_name=resource_name,
                action=action,
                duration_ms=max(1, int((time.perf_counter() - start) * 1000)),
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

    def get_status(self) -> dict[str, object]:
        running = bool(self._task and not self._task.done())
        refresh_running = bool(self._refresh_task and not self._refresh_task.done())
        return {
            "running": running,
            "processing_loop_running": self.processing_loop_running,
            "refresh_loop_running": refresh_running,
            "stopping": self._stopping,
            "auth_blocked": self._auth_blocked_logged,
            "processing_last_heartbeat_at": self.processing_last_heartbeat_at.isoformat() if self.processing_last_heartbeat_at else None,
            "processing_last_success_at": self.processing_last_success_at.isoformat() if self.processing_last_success_at else None,
            "refresh_last_heartbeat_at": self.refresh_last_heartbeat_at.isoformat() if self.refresh_last_heartbeat_at else None,
            "refresh_last_success_at": self.refresh_last_success_at.isoformat() if self.refresh_last_success_at else None,
            "recovery_in_progress": self.recovery_in_progress,
            "backlog_detected": self.backlog_detected,
        }


def enqueue_contact(contact_id: int) -> int:
    session = get_session()
    try:
        record = enqueue_pending_sync(session, contact_id)
        return int(record.id)
    finally:
        session.close()


def get_worker() -> PendingSyncWorker:
    global pending_sync_worker
    return pending_sync_worker


pending_sync_worker = PendingSyncWorker()
