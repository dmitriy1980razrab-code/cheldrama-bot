from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from theatre_bot.campaigns import _eligible_rows
from theatre_bot.channels import OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.persistent_replies import enqueue_reply
from theatre_bot.subscribers import IdentityProtector


def _timestamp(at: datetime | None = None) -> str:
    value = at or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def queue_due_campaigns(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    now: datetime | None = None,
    recipient_limit: int = 1000,
    min_interval_days: int = 7,
) -> int:
    if recipient_limit < 1 or recipient_limit > 1000:
        raise ValueError("recipient limit must be between 1 and 1000")
    if min_interval_days < 0:
        raise ValueError("minimum interval cannot be negative")
    current = _timestamp(now)
    campaigns = connection.execute(
        """
        SELECT * FROM campaigns
        WHERE status = 'scheduled' AND scheduled_at <= ?
        ORDER BY scheduled_at, id
        """,
        (current,),
    ).fetchall()
    queued = 0
    remaining = recipient_limit
    for campaign in campaigns:
        if remaining <= 0:
            break
        existing_subscribers = {
            row["subscriber_id"]
            for row in connection.execute(
                "SELECT subscriber_id FROM campaign_recipients WHERE campaign_id = ?",
                (campaign["id"],),
            ).fetchall()
        }
        eligible = [
            row for row in _eligible_rows(
                connection, campaign, now, min_interval_days
            )
            if row["id"] not in existing_subscribers
        ][:remaining]
        with connection:
            for subscriber in eligible:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO campaign_recipients (
                        campaign_id, subscriber_id, status
                    ) VALUES (?, ?, 'planned')
                    """,
                    (campaign["id"], subscriber["id"]),
                )
        planned = connection.execute(
            """
            SELECT cr.id AS recipient_id, s.channel, s.external_id_encrypted
            FROM campaign_recipients cr
            JOIN subscribers s ON s.id = cr.subscriber_id
            WHERE cr.campaign_id = ? AND cr.status = 'planned'
            ORDER BY cr.id
            """,
            (campaign["id"],),
        ).fetchall()
        for recipient in planned:
            message = OutgoingMessage(
                recipient["channel"],
                protector.decrypt(recipient["external_id_encrypted"]),
                Reply(campaign["message"]),
            )
            if enqueue_reply(
                connection,
                protector,
                message,
                dedupe_key=f"campaign-recipient:{recipient['recipient_id']}",
                at=now,
                source_type="campaign",
                source_key=str(recipient["recipient_id"]),
            ):
                queued += 1
                remaining -= 1
                if remaining <= 0:
                    break
        recipient_count = connection.execute(
            "SELECT count(*) FROM campaign_recipients WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()[0]
        if recipient_count == 0:
            with connection:
                connection.execute(
                    """
                    UPDATE campaigns SET status = 'completed'
                    WHERE id = ? AND status = 'scheduled'
                    """,
                    (campaign["id"],),
                )
    return queued
