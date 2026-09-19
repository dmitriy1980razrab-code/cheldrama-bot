from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
import re
import time


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,100}$")


def valid_session_id(value: object) -> bool:
    return isinstance(value, str) and bool(SESSION_ID_PATTERN.fullmatch(value))


@dataclass(frozen=True)
class Turn:
    user_text: str
    bot_text: str
    created_at: float


class ConversationStore:
    def __init__(self, max_turns: int = 4, ttl_seconds: int = 1800) -> None:
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, deque[Turn]] = defaultdict(lambda: deque(maxlen=max_turns))
        self._last_seen: dict[str, float] = {}
        self._lock = Lock()

    def add(self, session_id: str, user_text: str, bot_text: str, now: float | None = None) -> None:
        timestamp = now if now is not None else time.monotonic()
        with self._lock:
            self._purge(timestamp)
            self._items[session_id].append(Turn(user_text, bot_text, timestamp))
            self._last_seen[session_id] = timestamp

    def get(self, session_id: str, now: float | None = None) -> tuple[Turn, ...]:
        timestamp = now if now is not None else time.monotonic()
        with self._lock:
            self._purge(timestamp)
            return tuple(self._items.get(session_id, ()))

    def _purge(self, now: float) -> None:
        expired = [
            session_id
            for session_id, last_seen in self._last_seen.items()
            if now - last_seen > self.ttl_seconds
        ]
        for session_id in expired:
            self._last_seen.pop(session_id, None)
            self._items.pop(session_id, None)


class RateLimiter:
    def __init__(self, limit: int = 20, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        timestamp = now if now is not None else time.monotonic()
        threshold = timestamp - self.window_seconds
        with self._lock:
            requests = self._requests[key]
            while requests and requests[0] <= threshold:
                requests.popleft()
            if len(requests) >= self.limit:
                return False
            requests.append(timestamp)
            return True

