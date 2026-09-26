from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sqlite3

from theatre_bot.subscribers import IdentityProtector


THEATRE_TIMEZONE = timezone(timedelta(hours=5))


@dataclass(frozen=True)
class NotificationBuildReport:
    rescheduled: int
    removed: int
    reminders: int


@dataclass(frozen=True)
class PendingNotification:
    queue_id: int
    channel: str
    external_id: str
    notification_type: str
    message: str


def _timestamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=THEATRE_TIMEZONE)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _local_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=THEATRE_TIMEZONE)
    return parsed.astimezone(THEATRE_TIMEZONE)


def _active_subscriptions(
    connection: sqlite3.Connection,
    play_key: str,
    notification_flag: str,
) -> list[sqlite3.Row]:
    if notification_flag not in {"notify_changes", "remind_24h"}:
        raise ValueError("unknown notification flag")
    return connection.execute(
        f"""
        SELECT sub.id AS subscription_id, sub.subscriber_id
        FROM subscriptions sub
        JOIN subscribers s ON s.id = sub.subscriber_id
        WHERE sub.topic_type = 'play' AND sub.topic_key = ?
          AND sub.status = 'active' AND sub.{notification_flag} = 1
          AND s.status = 'active'
          AND (
              SELECT action FROM consent_events c
              WHERE c.subscriber_id = s.id
                AND c.consent_type = 'service_notifications'
              ORDER BY c.id DESC LIMIT 1
          ) = 'granted'
        """,
        (play_key,),
    ).fetchall()


def _queue(
    connection: sqlite3.Connection,
    subscriber_id: int,
    subscription_id: int,
    performance_key: str,
    notification_type: str,
    message: str,
    created_at: str,
) -> bool:
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO notification_queue (
            subscriber_id, subscription_id, performance_key,
            notification_type, message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            subscriber_id, subscription_id, performance_key,
            notification_type, message, created_at,
        ),
    )
    return cursor.rowcount == 1


def build_service_notifications(
    theatre_connection: sqlite3.Connection,
    subscriber_connection: sqlite3.Connection,
    now: datetime | None = None,
) -> NotificationBuildReport:
    current = now or datetime.now(THEATRE_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=THEATRE_TIMEZONE)
    current = current.astimezone(THEATRE_TIMEZONE)
    created_at = _timestamp(current)
    counts = {"rescheduled": 0, "removed": 0, "reminder_24h": 0}
    performances = theatre_connection.execute(
        """
        SELECT p.source_key, p.starts_at, p.status,
               pl.source_url AS play_key, pl.title AS play_title
        FROM performances p JOIN plays pl ON pl.id = p.play_id
        WHERE p.status IN ('scheduled', 'removed')
        ORDER BY p.starts_at, p.id
        """
    ).fetchall()
    with subscriber_connection:
        for performance in performances:
            observation = subscriber_connection.execute(
                "SELECT * FROM performance_observations WHERE source_key = ?",
                (performance["source_key"],),
            ).fetchone()
            starts_at = _local_datetime(performance["starts_at"])

            if observation and observation["starts_at"] != performance["starts_at"]:
                old_time = _local_datetime(observation["starts_at"])
                message = (
                    f"Время спектакля «{performance['play_title']}» изменилось: "
                    f"было {old_time.strftime('%d.%m.%Y в %H:%M')}, "
                    f"стало {starts_at.strftime('%d.%m.%Y в %H:%M')}."
                )
                for subscription in _active_subscriptions(
                    subscriber_connection, performance["play_key"], "notify_changes"
                ):
                    if _queue(
                        subscriber_connection, subscription["subscriber_id"],
                        subscription["subscription_id"], performance["source_key"],
                        "rescheduled", message, created_at,
                    ):
                        counts["rescheduled"] += 1

            if (
                observation
                and observation["status"] == "scheduled"
                and performance["status"] == "removed"
            ):
                message = (
                    f"Показ спектакля «{performance['play_title']}» "
                    f"{starts_at.strftime('%d.%m.%Y в %H:%M')} снят с опубликованной афиши. "
                    "Уточните информацию у театра."
                )
                for subscription in _active_subscriptions(
                    subscriber_connection, performance["play_key"], "notify_changes"
                ):
                    if _queue(
                        subscriber_connection, subscription["subscriber_id"],
                        subscription["subscription_id"], performance["source_key"],
                        "removed", message, created_at,
                    ):
                        counts["removed"] += 1

            delta = starts_at - current
            if performance["status"] == "scheduled" and timedelta(hours=23, minutes=30) <= delta <= timedelta(hours=24, minutes=30):
                message = (
                    f"Напоминаем: спектакль «{performance['play_title']}» состоится завтра, "
                    f"{starts_at.strftime('%d.%m.%Y в %H:%M')}. Будем рады видеть Вас в театре!"
                )
                for subscription in _active_subscriptions(
                    subscriber_connection, performance["play_key"], "remind_24h"
                ):
                    if _queue(
                        subscriber_connection, subscription["subscriber_id"],
                        subscription["subscription_id"], performance["source_key"],
                        "reminder_24h", message, created_at,
                    ):
                        counts["reminder_24h"] += 1

            subscriber_connection.execute(
                """
                INSERT INTO performance_observations (
                    source_key, play_key, play_title, starts_at, status, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    play_key = excluded.play_key,
                    play_title = excluded.play_title,
                    starts_at = excluded.starts_at,
                    status = excluded.status,
                    observed_at = excluded.observed_at
                """,
                (
                    performance["source_key"], performance["play_key"],
                    performance["play_title"], performance["starts_at"],
                    performance["status"], created_at,
                ),
            )
    return NotificationBuildReport(
        counts["rescheduled"], counts["removed"], counts["reminder_24h"]
    )


def pending_notifications(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    limit: int = 100,
) -> tuple[PendingNotification, ...]:
    rows = connection.execute(
        """
        SELECT q.id, q.notification_type, q.message,
               s.channel, s.external_id_encrypted
        FROM notification_queue q
        JOIN subscribers s ON s.id = q.subscriber_id
        JOIN subscriptions sub ON sub.id = q.subscription_id
        WHERE q.status = 'pending' AND s.status = 'active'
          AND sub.status = 'active'
          AND s.external_id_encrypted IS NOT NULL
          AND (
              SELECT action FROM consent_events c
              WHERE c.subscriber_id = s.id
                AND c.consent_type = 'service_notifications'
              ORDER BY c.id DESC LIMIT 1
          ) = 'granted'
        ORDER BY q.id LIMIT ?
        """,
        (max(1, min(limit, 1000)),),
    ).fetchall()
    return tuple(
        PendingNotification(
            row["id"], row["channel"],
            protector.decrypt(row["external_id_encrypted"]),
            row["notification_type"], row["message"],
        )
        for row in rows
    )


def mark_notification_result(
    connection: sqlite3.Connection,
    queue_id: int,
    success: bool,
    failure_reason: str | None = None,
    at: datetime | None = None,
) -> None:
    timestamp = _timestamp(at or datetime.now(timezone.utc)) if success else None
    reason = None if success else (failure_reason or "delivery_failed")[:120]
    with connection:
        connection.execute(
            """
            UPDATE notification_queue
            SET status = ?, sent_at = ?, failure_reason = ?
            WHERE id = ? AND status = 'pending'
            """,
            ("sent" if success else "failed", timestamp, reason, queue_id),
        )
