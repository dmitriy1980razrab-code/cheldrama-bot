from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Callable, Protocol

from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Card, Reply
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
)


@dataclass(frozen=True)
class StoredReply:
    queue_id: int
    message: OutgoingMessage


@dataclass(frozen=True)
class PersistentDeliveryReport:
    sent: int
    failed: int


class ReplySender(Protocol):
    def send(self, message: OutgoingMessage) -> None:
        """Передать ответ адаптеру канала."""


def _timestamp(moment: datetime | None = None) -> str:
    value = moment or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _serialize(message: OutgoingMessage) -> bytes:
    payload = {
        "channel": message.channel,
        "external_user_id": message.external_user_id,
        "reply": {
            "text": message.reply.text,
            "cards": [asdict(card) for card in message.reply.cards],
        },
        "buttons": [asdict(button) for button in message.buttons],
    }
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _deserialize(payload: bytes) -> OutgoingMessage:
    value = json.loads(payload.decode("utf-8"))
    return OutgoingMessage(
        channel=value["channel"],
        external_user_id=value["external_user_id"],
        reply=Reply(
            value["reply"]["text"],
            tuple(Card(**card) for card in value["reply"].get("cards", [])),
        ),
        buttons=tuple(OutgoingButton(**button) for button in value.get("buttons", [])),
    )


def enqueue_reply(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    message: OutgoingMessage,
    dedupe_key: str,
    at: datetime | None = None,
) -> bool:
    if message.channel not in {"vk", "max"} or not dedupe_key:
        raise ValueError("valid channel and dedupe key are required")
    dedupe_hash = protector.lookup_hash(
        message.channel, f"outgoing-reply:{dedupe_key}"
    )
    encrypted = protector.encrypt(_serialize(message).decode("utf-8"))
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO outgoing_reply_queue (
                channel, dedupe_hash, payload_encrypted, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (message.channel, dedupe_hash, encrypted, _timestamp(at)),
        )
    return cursor.rowcount == 1


def claim_replies(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    limit: int = 100,
    now: datetime | None = None,
    lease: timedelta = timedelta(minutes=5),
    max_attempts: int = 3,
) -> tuple[StoredReply, ...]:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    current = _timestamp(moment)
    locked_until = _timestamp(moment + lease)
    connection.execute("BEGIN IMMEDIATE")
    try:
        rows = connection.execute(
            """
            SELECT id, payload_encrypted FROM outgoing_reply_queue
            WHERE attempt_count < ? AND (
                (status IN ('pending', 'failed')
                 AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                OR (status = 'processing' AND locked_until <= ?)
            )
            ORDER BY id LIMIT ?
            """,
            (max(1, max_attempts), current, current, max(1, min(limit, 1000))),
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                UPDATE outgoing_reply_queue
                SET status = 'processing', locked_until = ?
                WHERE id = ?
                """,
                (locked_until, row["id"]),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(
        StoredReply(
            row["id"],
            _deserialize(protector.decrypt(row["payload_encrypted"]).encode("utf-8")),
        )
        for row in rows
    )


def mark_reply_result(
    connection: sqlite3.Connection,
    queue_id: int,
    success: bool,
    failure_reason: str | None = None,
    at: datetime | None = None,
) -> None:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    timestamp = _timestamp(moment)
    with connection:
        row = connection.execute(
            "SELECT attempt_count FROM outgoing_reply_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()
        if row is None:
            return
        attempts = row["attempt_count"] + 1
        if success:
            connection.execute(
                """
                UPDATE outgoing_reply_queue
                SET status = 'sent', attempt_count = ?, sent_at = ?,
                    last_attempt_at = ?, next_attempt_at = NULL,
                    locked_until = NULL, failure_reason = NULL
                WHERE id = ? AND status = 'processing'
                """,
                (attempts, timestamp, timestamp, queue_id),
            )
        else:
            delay = min(60, 5 * (2 ** (attempts - 1)))
            connection.execute(
                """
                UPDATE outgoing_reply_queue
                SET status = 'failed', attempt_count = ?, sent_at = NULL,
                    last_attempt_at = ?, next_attempt_at = ?, locked_until = NULL,
                    failure_reason = ?
                WHERE id = ? AND status = 'processing'
                """,
                (
                    attempts,
                    timestamp,
                    _timestamp(moment + timedelta(minutes=delay)),
                    (failure_reason or "delivery_failed")[:120],
                    queue_id,
                ),
            )


def deliver_persistent_replies(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    sender: ReplySender,
    limit: int = 100,
    now: datetime | None = None,
) -> PersistentDeliveryReport:
    sent = 0
    failed = 0
    for stored in claim_replies(connection, protector, limit=limit, now=now):
        try:
            sender.send(stored.message)
        except Exception as error:
            mark_reply_result(
                connection,
                stored.queue_id,
                False,
                failure_reason=type(error).__name__,
                at=now,
            )
            failed += 1
        else:
            mark_reply_result(connection, stored.queue_id, True, at=now)
            sent += 1
    return PersistentDeliveryReport(sent, failed)


class PersistentReplyExecutor:
    """Сохраняет ответ до подтверждения Webhook, не выполняя сетевой запрос."""

    def __init__(
        self,
        database_path: Path,
        protector: IdentityProtector,
        notify: Callable[[], None] | None = None,
    ) -> None:
        self._database_path = database_path
        self._protector = protector
        self._notify = notify

    def submit(
        self, message: OutgoingMessage, dedupe_key: str | None = None
    ) -> bool:
        if not dedupe_key:
            return False
        connection = connect_subscribers(self._database_path)
        try:
            initialize_subscribers(connection)
            enqueue_reply(
                connection, self._protector, message, dedupe_key
            )
            if self._notify is not None:
                self._notify()
            # Повтор уже сохранён и также считается безопасно принятым.
            return True
        except sqlite3.Error:
            return False
        finally:
            connection.close()
