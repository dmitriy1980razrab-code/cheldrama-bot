"""Per-user throttling, with temporary state and no stored message text or raw IDs."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
import unicodedata

from theatre_bot.subscribers import IdentityProtector

MAX_MESSAGE_CHARS = 1000


def normalize_message(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


@dataclass(frozen=True)
class SpamPolicy:
    text_limit: int = 20
    action_limit: int = 60
    repeat_limit: int = 5
    window_seconds: int = 60
    repeat_window_seconds: int = 120
    block_seconds: int = 600
    retention_seconds: int = 86400

    def __post_init__(self):
        if any(value < 1 for value in self.__dict__.values()):
            raise ValueError('spam protection limits must be positive')
        if self.retention_seconds < self.block_seconds:
            raise ValueError('retention must cover the temporary block')


@dataclass(frozen=True)
class SpamDecision:
    allowed: bool
    warn: bool = False


@contextmanager
def _transaction(connection):
    if connection.in_transaction:
        yield
        return
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def check_message(connection: sqlite3.Connection, protector: IdentityProtector,
                  channel: str, external_id: str, text: str,
                  is_action: bool = False, now: datetime | None = None,
                  policy: SpamPolicy | None = None) -> SpamDecision:
    if channel not in {'vk', 'max'}:
        raise ValueError('unsupported channel')
    policy = policy or SpamPolicy()
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    timestamp = moment.timestamp()
    actor = protector.lookup_hash(channel, 'spam-actor:' + external_id)
    # A keyed hash prevents guessing a common phrase from its stored fingerprint.
    fingerprint = None if is_action else protector.lookup_hash(channel, 'spam-text:' + normalize_message(text))
    with _transaction(connection):
        connection.execute('DELETE FROM spam_guard_state WHERE last_seen < ? AND blocked_until <= ?',
                           (timestamp-policy.retention_seconds, timestamp))
        row = connection.execute('SELECT * FROM spam_guard_state WHERE actor_hash=?', (actor,)).fetchone()
        if row is not None and row['blocked_until'] > timestamp:
            # Do not extend the pause or create more warnings for ignored messages.
            return SpamDecision(False)
        if row is None or row['blocked_until'] > 0:
            text_tokens = float(policy.text_limit)
            action_tokens = float(policy.action_limit)
            last_refill = timestamp
            repeat_hash, repeat_count, repeat_since = None, 0, timestamp
        else:
            elapsed = max(0, timestamp-row['last_refill'])
            text_tokens = min(policy.text_limit, row['text_tokens']+elapsed*policy.text_limit/policy.window_seconds)
            action_tokens = min(policy.action_limit, row['action_tokens']+elapsed*policy.action_limit/policy.window_seconds)
            last_refill = max(timestamp, row['last_refill'])
            repeat_hash, repeat_count, repeat_since = row['repeat_hash'], row['repeat_count'], row['repeat_since']
        blocked = False
        if is_action:
            blocked = action_tokens < 1
            action_tokens = max(0.0, action_tokens-1)
        else:
            blocked = text_tokens < 1
            text_tokens = max(0.0, text_tokens-1)
            if repeat_hash != fingerprint or timestamp-repeat_since >= policy.repeat_window_seconds:
                repeat_hash, repeat_count, repeat_since = fingerprint, 1, timestamp
            else:
                repeat_count += 1
            blocked = blocked or repeat_count >= policy.repeat_limit
        connection.execute('''INSERT INTO spam_guard_state
            (actor_hash, text_tokens, action_tokens, last_refill,
             repeat_hash, repeat_count, repeat_since, blocked_until, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(actor_hash) DO UPDATE SET
              text_tokens=excluded.text_tokens, action_tokens=excluded.action_tokens,
              last_refill=excluded.last_refill, repeat_hash=excluded.repeat_hash,
              repeat_count=excluded.repeat_count, repeat_since=excluded.repeat_since,
              blocked_until=excluded.blocked_until, last_seen=excluded.last_seen''',
            (actor, text_tokens, action_tokens, last_refill, repeat_hash, repeat_count,
             repeat_since, timestamp+policy.block_seconds if blocked else 0, timestamp))
        return SpamDecision(not blocked, warn=blocked)
