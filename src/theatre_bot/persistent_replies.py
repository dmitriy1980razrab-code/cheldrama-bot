from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import hashlib
import secrets
from pathlib import Path
import sqlite3
from typing import Callable, Protocol

from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.outgoing_delivery import DeliveryError
from theatre_bot.dialog import Card, PerformanceChoice, Reply
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



@contextmanager
def _transaction(connection: sqlite3.Connection):
    # Start explicitly: Connection.__enter__ alone does not begin a transaction.
    if connection.in_transaction:
        yield
        return
    connection.execute("BEGIN")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _allocate_vk_random_id(connection: sqlite3.Connection) -> int:
    # Called while holding BEGIN IMMEDIATE. IDs in retained queue rows never collide.
    for _ in range(100):
        value = secrets.randbelow(2_147_483_647) + 1
        exists = connection.execute(
            "SELECT 1 FROM outgoing_reply_queue WHERE vk_random_id = ?", (value,)
        ).fetchone()
        if exists is None:
            return value
    raise RuntimeError("cannot allocate a unique VK delivery identifier")


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
            "choices": [asdict(choice) for choice in message.reply.choices],
        },
        "buttons": [asdict(button) for button in message.buttons],
        "keyboard_inline": message.keyboard_inline,
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
            tuple(PerformanceChoice(**choice) for choice in value["reply"].get("choices", [])),
        ),
        buttons=tuple(OutgoingButton(**button) for button in value.get("buttons", [])),
        keyboard_inline=value.get("keyboard_inline", True),
    )


def enqueue_reply(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    message: OutgoingMessage,
    dedupe_key: str,
    at: datetime | None = None,
    source_type: str = "webhook",
    source_key: str | None = None,
    available_at: datetime | None = None,
) -> bool:
    if message.channel not in {"vk", "max"} or not dedupe_key:
        raise ValueError("valid channel and dedupe key are required")
    if source_type not in {"webhook", "service_notification", "campaign"}:
        raise ValueError("unknown reply source")
    dedupe_hash = protector.lookup_hash(
        message.channel, f"outgoing-reply:{dedupe_key}"
    )
    encrypted = protector.encrypt(_serialize(message).decode("utf-8"))
    with _transaction(connection):
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO outgoing_reply_queue (
                channel, dedupe_hash, payload_encrypted,
                source_type, source_key, created_at, next_attempt_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.channel, dedupe_hash, encrypted,
                source_type, source_key, _timestamp(at),
                _timestamp(available_at) if available_at is not None else None,
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
        uncertain = connection.execute(
            """
            SELECT id, payload_encrypted, source_type, source_key
            FROM outgoing_reply_queue
            WHERE channel = 'max' AND status = 'processing' AND locked_until <= ?
              AND source_type IN ('service_notification', 'campaign')
            """, (current,),
        ).fetchall()
        for row in uncertain:
            stored = StoredReply(row["id"], _deserialize(
                protector.decrypt(row["payload_encrypted"]).encode("utf-8")
            ), row["source_type"], row["source_key"])
            _mark_uncertain(connection, stored, at=moment)
        rows = connection.execute(
            """
            SELECT id, channel, vk_random_id, payload_encrypted, source_type, source_key
            FROM outgoing_reply_queue
            WHERE attempt_count < ?
              AND COALESCE(failure_reason, '') != 'delivery_uncertain' AND (
                (status IN ('pending', 'failed')
                 AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                OR (status = 'processing' AND locked_until <= ?)
            )
            ORDER BY id LIMIT ?
            """,
            (max(1, max_attempts), current, current, max(1, min(limit, 1000))),
        ).fetchall()
        random_ids = {}
        for row in rows:
            random_id = row["vk_random_id"]
            if row["channel"] == "vk" and random_id is None:
                random_id = _allocate_vk_random_id(connection)
                connection.execute(
                    "UPDATE outgoing_reply_queue SET vk_random_id = ? WHERE id = ?",
                    (random_id, row["id"]),
                )
            random_ids[row["id"]] = random_id
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
            replace(
                _deserialize(protector.decrypt(row["payload_encrypted"]).encode("utf-8")),
                vk_random_id=random_ids[row["id"]],
            ),
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
    with _transaction(connection):
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


def _critical_max(stored: StoredReply) -> bool:
    return stored.message.channel == "max" and stored.source_type in {
        "service_notification", "campaign",
    }


def _mark_uncertain(
    connection: sqlite3.Connection,
    stored: StoredReply,
    at: datetime | None = None,
) -> None:
    # No automatic retry: acceptance could have happened before the crash/timeout.
    with _transaction(connection):
        connection.execute(
            """
            UPDATE outgoing_reply_queue
            SET status = 'failed', attempt_count = MAX(attempt_count, 3),
                failure_reason = 'delivery_uncertain', locked_until = NULL,
                last_attempt_at = ?, next_attempt_at = NULL
            WHERE id = ? AND status = 'processing'
            """, (_timestamp(at), stored.queue_id),
        )
        _mark_source_result(connection, stored, False, "delivery_uncertain", at=at)


def deliver_persistent_replies(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    sender: ReplySender,
    limit: int = 100,
    now: datetime | None = None,
    *, theatre_connection: sqlite3.Connection | None = None,
) -> PersistentDeliveryReport:
    purge_delivered_reply_payloads(connection, now=now)
    sent = 0
    failed = 0
    cancelled = 0
    for stored in claim_replies(connection, protector, limit=limit, now=now):
        if not _source_allows_delivery(connection, stored, theatre_connection=theatre_connection, now=now):
            with _transaction(connection):
                _cancel_reply(connection, stored, at=now)
            cancelled += 1
            continue
        try:
            sender.send(stored.message)
        except Exception as error:
            ambiguous = _critical_max(stored) and (
                not isinstance(error, DeliveryError) or error.status >= 500
            )
            if ambiguous:
                _mark_uncertain(connection, stored, at=now)
            else:
                with _transaction(connection):
                    mark_reply_result(
                        connection, stored.queue_id, False,
                        failure_reason=type(error).__name__, at=now,
                    )
                    _mark_source_result(
                        connection, stored, False, type(error).__name__, at=now,
                    )
            failed += 1
        else:
            with _transaction(connection):
                mark_reply_result(connection, stored.queue_id, True, at=now)
                _mark_source_result(connection, stored, True, None, at=now)
            sent += 1
    return PersistentDeliveryReport(sent, failed, cancelled)


def _source_allows_delivery(
    connection: sqlite3.Connection,
    stored: StoredReply,
    *, theatre_connection: sqlite3.Connection | None = None,
    now: datetime | None = None,
) -> bool:
    if stored.source_type == "webhook" and stored.source_key and stored.source_key.startswith("staff-alert:"):
        parts = stored.source_key.split(":")
        if len(parts) != 4 or not parts[1].isdigit():
            return False
        account = connection.execute(
            "SELECT notify_id_encrypted FROM admin_users WHERE id=? AND active=1 AND role=? AND notify_channel=? AND notify_id_encrypted IS NOT NULL",
            (int(parts[1]), parts[2], stored.message.channel),
        ).fetchone()
        return bool(account and hashlib.sha256(account["notify_id_encrypted"]).hexdigest() == parts[3])
    if stored.source_type == "campaign":
        if not stored.source_key or not stored.source_key.isdigit():
            return False
        row = connection.execute(
            """
            SELECT cr.id
            FROM campaign_recipients cr
            JOIN campaigns c ON c.id = cr.campaign_id
            JOIN subscribers s ON s.id = cr.subscriber_id
            WHERE cr.id = ? AND cr.status = 'planned'
              AND c.status = 'scheduled'
              AND s.status = 'active' AND s.external_id_encrypted IS NOT NULL
              AND (c.premieres_only=0 OR s.interested_in_premieres=1)
              AND (c.target_type='all' OR EXISTS (
                  SELECT 1 FROM preferences p WHERE p.subscriber_id=s.id
                    AND p.preference_type=c.target_type AND p.preference_key=c.target_key
              ))
              AND NOT EXISTS (
                  SELECT 1 FROM campaign_recipients recent WHERE recent.subscriber_id=s.id
                    AND recent.status='sent' AND recent.id!=cr.id
                    AND julianday(recent.sent_at)>=julianday(?)-7
              )
              AND (
                  SELECT action FROM consent_events ce
                  WHERE ce.subscriber_id = s.id
                    AND ce.consent_type = 'marketing'
                  ORDER BY ce.id DESC LIMIT 1
              ) = 'granted'
            """,
            (int(stored.source_key), _timestamp(now)),
        ).fetchone()
        return row is not None
    if stored.source_type != "service_notification":
        return True
    if not stored.source_key or not stored.source_key.isdigit():
        return False
    row = connection.execute(
        """
        SELECT q.id, q.notification_type, q.planned_starts_at, q.performance_key
        FROM notification_queue q
        JOIN subscribers s ON s.id = q.subscriber_id
        JOIN subscriptions sub ON sub.id = q.subscription_id
        WHERE q.id = ? AND q.status IN ('pending', 'failed')
          AND s.status = 'active' AND sub.status = 'active'
          AND s.external_id_encrypted IS NOT NULL
          AND CASE WHEN q.notification_type = 'reminder_24h' THEN sub.remind_24h ELSE sub.notify_changes END = 1
          AND (
              SELECT action FROM consent_events c
              WHERE c.subscriber_id = s.id
                AND c.consent_type = 'service_notifications'
              ORDER BY c.id DESC LIMIT 1
          ) = 'granted'
        """,
        (int(stored.source_key),),
    ).fetchone()
    if row is None:
        return False
    if row["notification_type"] == "reminder_24h" and row["planned_starts_at"] and theatre_connection is not None:
        performance = theatre_connection.execute(
            "SELECT starts_at, status FROM performances WHERE source_key = ?", (row["performance_key"],)
        ).fetchone()
        if performance is None or performance["status"] != "scheduled" or performance["starts_at"] != row["planned_starts_at"]:
            return False
        from theatre_bot.notifications import _local_datetime
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        # A resumed worker must not send a "tomorrow" reminder hours late.
        if moment > _local_datetime(performance["starts_at"]) - timedelta(hours=23, minutes=30):
            return False
    return True


def _cancel_reply(
    connection: sqlite3.Connection,
    stored: StoredReply,
    at: datetime | None = None,
) -> None:
    with _transaction(connection):
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
        elif stored.source_type == "campaign" and stored.source_key and stored.source_key.isdigit():
            connection.execute(
                """
                UPDATE campaign_recipients
                SET status = 'skipped', reason = 'marketing_not_allowed'
                WHERE id = ? AND status = 'planned'
                """,
                (int(stored.source_key),),
            )
    if stored.source_type == "campaign":
        _finalize_campaign_for_recipient(connection, stored.source_key)


def _mark_source_result(
    connection: sqlite3.Connection,
    stored: StoredReply,
    success: bool,
    failure_reason: str | None,
    at: datetime | None = None,
) -> None:
    if stored.source_type == "campaign":
        if not stored.source_key or not stored.source_key.isdigit():
            return
        recipient_id = int(stored.source_key)
        if success:
            with _transaction(connection):
                connection.execute(
                    """
                    UPDATE campaign_recipients
                    SET status = 'sent', reason = NULL, sent_at = ?
                    WHERE id = ? AND status = 'planned'
                    """,
                    (_timestamp(at), recipient_id),
                )
        else:
            outgoing = connection.execute(
                "SELECT attempt_count FROM outgoing_reply_queue WHERE id = ?",
                (stored.queue_id,),
            ).fetchone()
            if outgoing and outgoing["attempt_count"] >= 3:
                with _transaction(connection):
                    connection.execute(
                        """
                        UPDATE campaign_recipients
                        SET status = 'failed', reason = ?
                        WHERE id = ? AND status = 'planned'
                        """,
                        ((failure_reason or "delivery_failed")[:120], recipient_id),
                    )
        _finalize_campaign_for_recipient(connection, stored.source_key)
        return
    if stored.source_type != "service_notification" or not stored.source_key:
        return
    if not stored.source_key.isdigit():
        return
    if success:
        with _transaction(connection):
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
        with _transaction(connection):
            connection.execute(
                """
                UPDATE notification_queue
                SET status = 'failed', failure_reason = ?,
                    attempt_count = MAX(attempt_count, 3)
                WHERE id = ? AND status IN ('pending', 'failed')
                """,
                ((failure_reason or "delivery_failed")[:120], int(stored.source_key)),
            )


def _finalize_campaign_for_recipient(
    connection: sqlite3.Connection,
    source_key: str | None,
) -> None:
    if not source_key or not source_key.isdigit():
        return
    row = connection.execute(
        "SELECT campaign_id FROM campaign_recipients WHERE id = ?",
        (int(source_key),),
    ).fetchone()
    if row is None:
        return
    planned = connection.execute(
        """
        SELECT count(*) FROM campaign_recipients
        WHERE campaign_id = ? AND status = 'planned'
        """,
        (row["campaign_id"],),
    ).fetchone()[0]
    if planned == 0:
        with _transaction(connection):
            connection.execute(
                """
                UPDATE campaigns SET status = 'completed'
                WHERE id = ? AND status = 'scheduled'
                """,
                (row["campaign_id"],),
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


def purge_delivered_reply_payloads(connection, now=None):
    """Forget finished message bodies after three days; keep deduplication keys."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = _timestamp(moment - timedelta(days=3))
    with _transaction(connection):
        cursor = connection.execute("""
            UPDATE outgoing_reply_queue SET payload_encrypted = X''
            WHERE status IN ('sent', 'cancelled') AND length(payload_encrypted) > 0
              AND julianday(COALESCE(sent_at, last_attempt_at, created_at)) < julianday(?)
        """, (cutoff,))
    return cursor.rowcount
