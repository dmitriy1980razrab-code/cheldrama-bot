from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.notifications import (
    MemoryNotificationSender,
    build_service_notifications,
    execute_pending_notifications,
    mark_notification_result,
    pending_notifications,
)
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
    record_consent,
    subscribe_to_play,
    unsubscribe,
)


PLAY_URL = "https://www.cheldrama.ru/plays/hamlet/"
THEATRE_TZ = timezone(timedelta(hours=5))


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.theatre = connect(root / "theatre.sqlite3")
        initialize(self.theatre)
        self.subscribers = connect_subscribers(root / "subscribers.sqlite3")
        initialize_subscribers(self.subscribers)
        self.protector = IdentityProtector(Fernet.generate_key(), b"notify-key" * 4)
        self.now = datetime(2026, 10, 1, 19, 0, tzinfo=THEATRE_TZ)
        self.add_performance("2026-10-03T19:00")
        for consent_type in ("personal_data", "service_notifications"):
            record_consent(
                self.subscribers, self.protector, "vk", "vk-1",
                consent_type, "v1", True, "test", self.now,
                display_name="Анна",
            )
        subscribe_to_play(
            self.subscribers, self.protector, "vk", "vk-1",
            PLAY_URL, "Гамлет", self.now,
        )

    def tearDown(self):
        self.theatre.close()
        self.subscribers.close()
        self.tempdir.cleanup()

    def add_performance(self, starts_at):
        sync_affiche(
            self.theatre,
            [
                AfficheItem(
                    title="Гамлет",
                    play_url=PLAY_URL,
                    starts_at=starts_at,
                    genre="Драма",
                    age_rating="16+",
                    duration=None,
                    venue="Большая сцена",
                    image_url=None,
                    ticket_event_id="event-1",
                )
            ],
            now=self.now,
        )

    def test_reminder_is_created_24_hours_before_performance(self):
        report = build_service_notifications(
            self.theatre, self.subscribers,
            datetime(2026, 10, 2, 19, 0, tzinfo=THEATRE_TZ),
        )
        self.assertEqual(report.reminders, 1)
        pending = pending_notifications(self.subscribers, self.protector)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].external_id, "vk-1")
        self.assertIn("состоится завтра", pending[0].message)

    def test_reminder_is_not_duplicated(self):
        moment = datetime(2026, 10, 2, 19, 0, tzinfo=THEATRE_TZ)
        build_service_notifications(self.theatre, self.subscribers, moment)
        report = build_service_notifications(self.theatre, self.subscribers, moment)
        self.assertEqual(report.reminders, 0)

    def test_changed_time_creates_reschedule_notification(self):
        build_service_notifications(self.theatre, self.subscribers, self.now)
        self.theatre.execute(
            "UPDATE performances SET starts_at = '2026-10-03T20:00'"
        )
        self.theatre.commit()
        report = build_service_notifications(self.theatre, self.subscribers, self.now)
        self.assertEqual(report.rescheduled, 1)
        message = pending_notifications(self.subscribers, self.protector)[0].message
        self.assertIn("было 03.10.2026 в 19:00", message)
        self.assertIn("стало 03.10.2026 в 20:00", message)

    def test_removed_performance_creates_notice(self):
        build_service_notifications(self.theatre, self.subscribers, self.now)
        self.theatre.execute("UPDATE performances SET status = 'removed'")
        self.theatre.commit()
        report = build_service_notifications(self.theatre, self.subscribers, self.now)
        self.assertEqual(report.removed, 1)
        message = pending_notifications(self.subscribers, self.protector)[0].message
        self.assertIn("снят с опубликованной афиши", message)

    def test_unsubscribed_user_is_excluded_before_delivery(self):
        build_service_notifications(
            self.theatre, self.subscribers,
            datetime(2026, 10, 2, 19, 0, tzinfo=THEATRE_TZ),
        )
        unsubscribe(
            self.subscribers, self.protector, "vk", "vk-1",
            "v1", "test", self.now,
        )
        self.assertEqual(pending_notifications(self.subscribers, self.protector), ())

    def test_delivery_result_is_recorded(self):
        build_service_notifications(
            self.theatre, self.subscribers,
            datetime(2026, 10, 2, 19, 0, tzinfo=THEATRE_TZ),
        )
        notification = pending_notifications(self.subscribers, self.protector)[0]
        mark_notification_result(
            self.subscribers, notification.queue_id, True, at=self.now
        )
        row = self.subscribers.execute(
            "SELECT status, sent_at FROM notification_queue"
        ).fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertIsNotNone(row["sent_at"])

    def test_memory_sender_executes_queue_without_network(self):
        build_service_notifications(
            self.theatre, self.subscribers,
            datetime(2026, 10, 2, 19, 0, tzinfo=THEATRE_TZ),
        )
        sender = MemoryNotificationSender()
        report = execute_pending_notifications(
            self.subscribers, self.protector, sender, at=self.now
        )
        self.assertEqual((report.sent, report.failed), (1, 0))
        self.assertEqual(sender.deliveries[0][1], "vk-1")
        self.assertEqual(
            self.subscribers.execute(
                "SELECT status FROM notification_queue"
            ).fetchone()[0],
            "sent",
        )

    def test_delivery_failure_records_only_exception_type(self):
        class FailingSender:
            def send(self, channel, external_id, message):
                raise RuntimeError("secret external data")

        build_service_notifications(
            self.theatre, self.subscribers,
            datetime(2026, 10, 2, 19, 0, tzinfo=THEATRE_TZ),
        )
        report = execute_pending_notifications(
            self.subscribers, self.protector, FailingSender(), at=self.now
        )
        row = self.subscribers.execute(
            "SELECT status, failure_reason FROM notification_queue"
        ).fetchone()
        self.assertEqual((report.sent, report.failed), (0, 1))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["failure_reason"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
