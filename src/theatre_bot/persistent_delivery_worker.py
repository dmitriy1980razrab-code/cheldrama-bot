from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import threading
import time

from theatre_bot.persistent_replies import (
    PersistentDeliveryReport,
    ReplySender,
    deliver_persistent_replies,
)
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
)


@dataclass(frozen=True)
class PersistentWorkerStatus:
    running: bool
    cycles: int
    sent: int
    failed: int
    loop_errors: int
    last_cycle_at: str | None


@dataclass(frozen=True)
class ReplyQueueStatus:
    pending: int
    processing: int
    failed: int
    sent: int


def reply_queue_status(database_path: Path) -> ReplyQueueStatus:
    connection = connect_subscribers(database_path)
    try:
        initialize_subscribers(connection)
        counts = {row["status"]: row["count"] for row in connection.execute(
            """
            SELECT status, count(*) AS count
            FROM outgoing_reply_queue GROUP BY status
            """
        ).fetchall()}
    finally:
        connection.close()
    return ReplyQueueStatus(
        counts.get("pending", 0),
        counts.get("processing", 0),
        counts.get("failed", 0),
        counts.get("sent", 0),
    )


class PersistentDeliveryWorker:
    """Управляемый серверный цикл постоянной очереди ответов."""

    def __init__(
        self,
        database_path: Path,
        protector: IdentityProtector,
        sender: ReplySender,
        poll_interval: float = 5.0,
        batch_limit: int = 100,
        auto_start: bool = True,
    ) -> None:
        if poll_interval < 0.05 or poll_interval > 3600:
            raise ValueError("poll interval must be between 0.05 and 3600 seconds")
        if batch_limit < 1 or batch_limit > 1000:
            raise ValueError("batch limit must be between 1 and 1000")
        self._database_path = database_path
        self._protector = protector
        self._sender = sender
        self._poll_interval = poll_interval
        self._batch_limit = batch_limit
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cycles = 0
        self._sent = 0
        self._failed = 0
        self._loop_errors = 0
        self._last_cycle_at: str | None = None
        if auto_start:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stop.is_set():
                raise RuntimeError("persistent delivery worker is closed")
            self._thread = threading.Thread(
                target=self._run,
                name="theatre-persistent-delivery",
                daemon=True,
            )
            self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def run_once(self, now: datetime | None = None) -> PersistentDeliveryReport:
        with self._cycle_lock:
            connection = connect_subscribers(self._database_path)
            try:
                initialize_subscribers(connection)
                report = deliver_persistent_replies(
                    connection,
                    self._protector,
                    self._sender,
                    limit=self._batch_limit,
                    now=now,
                )
            finally:
                connection.close()
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        with self._lock:
            self._cycles += 1
            self._sent += report.sent
            self._failed += report.failed
            self._last_cycle_at = moment.astimezone(timezone.utc).isoformat(
                timespec="seconds"
            )
        return report

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                with self._lock:
                    self._loop_errors += 1
            self._wake.wait(self._poll_interval)
            self._wake.clear()

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() <= deadline:
            state = reply_queue_status(self._database_path)
            if state.pending == 0 and state.processing == 0:
                return True
            self.wake()
            time.sleep(0.01)
        return False

    def close(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()

    def status(self) -> PersistentWorkerStatus:
        with self._lock:
            thread = self._thread
            return PersistentWorkerStatus(
                bool(thread and thread.is_alive()),
                self._cycles,
                self._sent,
                self._failed,
                self._loop_errors,
                self._last_cycle_at,
            )
