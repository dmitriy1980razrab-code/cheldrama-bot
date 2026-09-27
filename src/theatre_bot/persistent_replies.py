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
    source_type: str
    source_key: str | None


@dataclass(frozen=True)
class PersistentDeliveryReport:
    sent: int
    failed: int
    cancelled: int = 0


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
    source_type: str = "webhook",
    source_key: str | None = None,
) -> bool:
    if message.channel not in {"vk", "max"} or not dedupe_key:
        raise ValueError("valid channel and dedupe key are required")
    if source_type not in {"webhook", "service_notification", "campaign"}:
        raise ValueError("unknown reply source")
    dedupe_hash = protector.lookup_hash(
        message.channel, f"outgoing-reply:{dedupe_key}"
    )
    encrypted = protector.encrypt(_serialize(message).decode("utf-8"))
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO outgoing_reply_queue (
                channel, dedupe_hash, payload_encrypted,
                source_type, source_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message.channel, dedupe_hash, encrypted,
                source_type, source_key, _timestamp(at),
            ),
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
            SELECT id, payload_encrypted, source_type, source_key
            FROM outgoing_reply_queue
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
            row["source_type"],
            row["source_key"],
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
    cancelled = 0
    for stored in claim_replies(connection, protector, limit=limit, now=now):
        if not _source_allows_delivery(connection, stored):
            _cancel_reply(connection, stored, at=now)
            cancelled += 1
            continue
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
            _mark_source_result(
                connection,
                stored,
                False,
                type(error).__name__,
                at=now,
            )
            failed += 1
        else:
            mark_reply_result(connection, stored.queue_id, True, at=now)
            _mark_source_result(connection, stored, True, None, at=now)
            sent += 1
    return PersistentDeliveryReport(sent, failed, cancelled)


def _source_allows_delivery(
    connection: sqlite3.Connection,
    stored: StoredReply,
) -> bool:
    if stored.source_type != "service_notification":
        return True
    if not stored.source_key or not stored.source_key.isdigit():
        return False
    row = connection.execute(
        """
        SELECT q.id
        FROM notification_queue q
        JOIN subscribers s ON s.id = q.subscriber_id
        JOIN subscriptions sub ON sub.id = q.subscription_id
        WHERE q.id = ? AND q.status IN ('pending', 'failed')
          AND s.status = 'active' AND sub.status = 'active'
          AND s.external_id_encrypted IS NOT NULL
          AND (
              SELECT action FROM consent_events c
              WHERE c.subscriber_id = s.id
                AND c.consent_type = 'service_notifications'
              ORDER BY c.id DESC LIMIT 1
          ) = 'granted'
        """,
        (int(stored.source_key),),
    ).fetchone()
    return row is not None


def _cancel_reply(
    connection: sqlite3.Connection,
    stored: StoredReply,
    at: datetime | None = None,
) -> None:
    with connection:
        connection.execute(
            """
            UPDATE outgoing_reply_queue
            SET status = 'cancelled', locked_until = NULL,
                failure_reason = 'source_not_allowed', last_attempt_at = ?
            WHERE id = ? AND status = 'processing'
            """,
            (_timestamp(at), stored.queue_id),
        )
        if stored.source_type == "service_notification" and stored.source_key and stored.source_key.isdigit():
            connection.execute(
                """
                UPDATE notification_queue
                SET status = 'cancelled', failure_reason = 'subscription_inactive'
                WHERE id = ? AND status IN ('pending', 'failed')
                """,
                (int(stored.source_key),),
            )


def _mark_source_result(
    connection: sqlite3.Connection,
    stored: StoredReply,
    success: bool,
    failure_reason: str | None,
    at: datetime | None = None,
) -> None:
    if stored.source_type != "service_notification" or not stored.source_key:
        return
    if not stored.source_key.isdigit():
        return
    if success:
        with connection:
            connection.execute(
                """
                UPDATE notification_queue
                SET status = 'sent', sent_at = ?, failure_reason = NULL
                WHERE id = ? AND status IN ('pending', 'failed')
                """,
                (_timestamp(at), int(stored.source_key)),
            )
        return
    outgoing = connection.execute(
        "SELECT attempt_count FROM outgoing_reply_queue WHERE id = ?",
        (stored.queue_id,),
    ).fetchone()
    if outgoing and outgoing["attempt_count"] >= 3:
        with connection:
            connection.execute(
                """
                UPDATE notification_queue
                SET status = 'failed', failure_reason = ?
                WHERE id = ? AND status IN ('pending', 'failed')
                """,
                ((failure_reason or "delivery_failed")[:120], int(stored.source_key)),
            )


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
