from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.notifications import build_service_notifications
from theatre_bot.outgoing_delivery import ChannelReplySender, MemoryHttpTransport, MaxApiSender
from theatre_bot.persistent_replies import deliver_persistent_replies
from theatre_bot.service_delivery import queue_service_notifications
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
    record_consent,
    subscribe_to_play,
    unsubscribe,
)


THEATRE_TZ = timezone(timedelta(hours=5))
PLAY_URL = "https://www.cheldrama.ru/plays/hamlet/"


class ServiceDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.theatre = connect(root / "theatre.sqlite3")
        initialize(self.theatre)
        self.subscribers = connect_subscribers(root / "subscribers.sqlite3")
        initialize_subscribers(self.subscribers)
        self.protector = IdentityProtector(Fernet.generate_key(), b"s" * 32)
        self.now = datetime(2026, 10, 1, 19, 0, tzinfo=THEATRE_TZ)
        self.delivery_time = self.now + timedelta(days=1)
        sync_affiche(
            self.theatre,
            [AfficheItem(
                title="Гамлет",
                play_url=PLAY_URL,
                starts_at="2026-10-03T19:00",
                genre="Драма",
                age_rating="16+",
                duration=None,
                venue="Большая сцена",
                image_url=None,
                ticket_event_id="event-1",
            )],
            now=self.now,
        )
        for consent_type in ("personal_data", "service_notifications"):
            record_consent(
                self.subscribers,
                self.protector,
                "max",
                "max-user-1",
                consent_type,
                "v1",
                True,
                "test",
                self.now,
            )
        subscribe_to_play(
            self.subscribers,
            self.protector,
            "max",
            "max-user-1",
            PLAY_URL,
            "Гамлет",
            self.now,
        )
        build_service_notifications(
            self.theatre, self.subscribers, self.delivery_time
        )

    def tearDown(self):
        self.theatre.close()
        self.subscribers.close()
        self.directory.cleanup()

    def _sender(self, status=200):
        transport = MemoryHttpTransport(status)
        sender = ChannelReplySender(max_sender=MaxApiSender("fake", transport))
        return sender, transport

    def test_notification_moves_to_encrypted_common_queue(self):
        self.assertEqual(queue_service_notifications(
            self.subscribers, self.protector, now=self.delivery_time
        ), 1)
        row = self.subscribers.execute(
            "SELECT source_type, source_key, payload_encrypted FROM outgoing_reply_queue"
        ).fetchone()
        self.assertEqual(row["source_type"], "service_notification")
        self.assertTrue(row["source_key"].isdigit())
        self.assertNotIn(b"max-user-1", row["payload_encrypted"])
        self.assertNotIn("Гамлет".encode("utf-8"), row["payload_encrypted"])

    def test_repeated_transfer_does_not_duplicate_reply(self):
        first = queue_service_notifications(
            self.subscribers, self.protector, now=self.delivery_time
        )
        second = queue_service_notifications(
            self.subscribers, self.protector, now=self.delivery_time
        )
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(self.subscribers.execute(
            "SELECT count(*) FROM outgoing_reply_queue"
        ).fetchone()[0], 1)

    def test_success_marks_both_queues_as_sent(self):
        queue_service_notifications(
            self.subscribers, self.protector, now=self.delivery_time
        )
        sender, transport = self._sender()
        report = deliver_persistent_replies(
            self.subscribers, self.protector, sender, now=self.delivery_time
        )
        statuses = (
            self.subscribers.execute(
                "SELECT status FROM outgoing_reply_queue"
            ).fetchone()[0],
            self.subscribers.execute(
                "SELECT status FROM notification_queue"
            ).fetchone()[0],
        )
        self.assertEqual((report.sent, len(transport.requests)), (1, 1))
        self.assertEqual(statuses, ("sent", "sent"))

    def test_unsubscribe_after_queue_cancels_delivery(self):
        queue_service_notifications(
            self.subscribers, self.protector, now=self.delivery_time
        )
        unsubscribe(
            self.subscribers,
            self.protector,
            "max",
            "max-user-1",
            "v1",
            "test",
            self.delivery_time,
        )
        sender, transport = self._sender()
        report = deliver_persistent_replies(
            self.subscribers, self.protector, sender, now=self.delivery_time
        )
        self.assertEqual((report.cancelled, len(transport.requests)), (1, 0))
        self.assertEqual(self.subscribers.execute(
            "SELECT status FROM notification_queue"
        ).fetchone()[0], "cancelled")

    def test_revoked_service_consent_cancels_delivery(self):
        queue_service_notifications(
            self.subscribers, self.protector, now=self.delivery_time
        )
        record_consent(
            self.subscribers,
            self.protector,
            "max",
            "max-user-1",
            "service_notifications",
            "v1",
            False,
            "test",
            self.delivery_time,
        )
        sender, transport = self._sender()
        report = deliver_persistent_replies(
            self.subscribers, self.protector, sender, now=self.delivery_time
        )
        self.assertEqual((report.cancelled, len(transport.requests)), (1, 0))

    def test_three_delivery_failures_mark_source_failed(self):
        queue_service_notifications(
            self.subscribers, self.protector, now=self.delivery_time
        )
        sender, _ = self._sender(503)
        for offset in (0, 5, 15):
            deliver_persistent_replies(
                self.subscribers,
                self.protector,
                sender,
                now=self.delivery_time + timedelta(minutes=offset),
            )
        row = self.subscribers.execute(
            "SELECT status, failure_reason FROM notification_queue"
        ).fetchone()
        self.assertEqual((row["status"], row["failure_reason"]), ("failed", "DeliveryError"))


if __name__ == "__main__":
    unittest.main()
