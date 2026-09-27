from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Full, Queue
import threading
import time
from typing import Protocol

from theatre_bot.channels import OutgoingMessage


class ReplySender(Protocol):
    def send(self, message: OutgoingMessage) -> None:
        """Передать единый ответ адаптеру соответствующего канала."""


@dataclass(frozen=True)
class ReplyWorkerStats:
    accepted: int
    sent: int
    failed: int
    rejected: int


class BackgroundReplyWorker:
    """Ограниченный фоновый исполнитель исходящих ответов."""

    def __init__(
        self,
        sender: ReplySender,
        capacity: int = 100,
        auto_start: bool = True,
    ) -> None:
        if capacity < 1 or capacity > 10_000:
            raise ValueError("reply queue capacity must be between 1 and 10000")
        self._sender = sender
        self._queue: Queue[OutgoingMessage] = Queue(maxsize=capacity)
        self._lock = threading.Lock()
        self._accepted = 0
        self._sent = 0
        self._failed = 0
        self._rejected = 0
        self._accepting = True
        self._started = False
        self._thread: threading.Thread | None = None
        if auto_start:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if not self._accepting:
                raise RuntimeError("reply worker is closed")
            self._started = True
            self._thread = threading.Thread(
                target=self._run,
                name="theatre-reply-worker",
                daemon=True,
            )
            self._thread.start()

    def submit(self, message: OutgoingMessage) -> bool:
        with self._lock:
            if not self._accepting:
                self._rejected += 1
                return False
            try:
                self._queue.put_nowait(message)
            except Full:
                self._rejected += 1
                return False
            self._accepted += 1
            return True

    def _run(self) -> None:
        while True:
            try:
                message = self._queue.get(timeout=0.1)
            except Empty:
                with self._lock:
                    if not self._accepting and self._queue.empty():
                        return
                continue
            try:
                self._sender.send(message)
            except Exception:
                with self._lock:
                    self._failed += 1
            else:
                with self._lock:
                    self._sent += 1
            finally:
                self._queue.task_done()

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() <= deadline:
            with self._lock:
                completed = self._sent + self._failed
                accepted = self._accepted
            if completed == accepted and self._queue.empty():
                return True
            time.sleep(0.01)
        return False

    def close(self, timeout: float = 5.0) -> bool:
        with self._lock:
            self._accepting = False
            thread = self._thread
        if thread is None:
            return self._queue.empty()
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()

    def stats(self) -> ReplyWorkerStats:
        with self._lock:
            return ReplyWorkerStats(
                self._accepted,
                self._sent,
                self._failed,
                self._rejected,
            )
