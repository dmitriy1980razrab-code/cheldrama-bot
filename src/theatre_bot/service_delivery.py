from __future__ import annotations

from datetime import datetime
import sqlite3

from theatre_bot.channels import OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.notifications import pending_notifications
from theatre_bot.persistent_replies import enqueue_reply
from theatre_bot.subscribers import IdentityProtector


def queue_service_notifications(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    limit: int = 100,
    now: datetime | None = None,
) -> int:
    queued = 0
    for notification in pending_notifications(
        connection, protector, limit=limit, now=now
    ):
        message = OutgoingMessage(
            notification.channel,
            notification.external_id,
            Reply(notification.message),
        )
        if enqueue_reply(
            connection,
            protector,
            message,
            dedupe_key=f"service-notification:{notification.queue_id}",
            at=now,
            source_type="service_notification",
            source_key=str(notification.queue_id),
        ):
            queued += 1
    return queued
