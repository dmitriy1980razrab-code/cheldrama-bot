from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.campaign_delivery import queue_due_campaigns
from theatre_bot.campaigns import approve_campaign, create_campaign, schedule_campaign
from theatre_bot.outgoing_delivery import ChannelReplySender, MemoryHttpTransport, MaxApiSender
from theatre_bot.persistent_replies import deliver_persistent_replies
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
    record_consent,
    subscribe_to_play,
    unsubscribe,
)


class CampaignDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connection = connect_subscribers(
            Path(self.directory.name) / "subscribers.sqlite3"
        )
        initialize_subscribers(self.connection)
        self.protector = IdentityProtector(Fernet.generate_key(), b"m" * 32)
        self.now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
        self.due = self.now + timedelta(hours=1)

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def add_subscriber(
        self,
        external_id="max-1",
        marketing=True,
        play_key="hamlet",
        play_title="Гамлет",
    ):
        for consent in ("personal_data", "service_notifications"):
            record_consent(
                self.connection, self.protector, "max", external_id,
                consent, "v1", True, "test", self.now,
            )
        if marketing:
            record_consent(
                self.connection, self.protector, "max", external_id,
                "marketing", "v1", True, "test", self.now,
            )
        subscribe_to_play(
            self.connection, self.protector, "max", external_id,
            play_key, play_title, self.now,
        )

    def scheduled_campaign(self, **overrides):
        values = {
            "name": "Предложение любителям Гамлета",
            "message": "Будем рады видеть Вас на спектакле!",
            "created_by": "admin",
            "channel": "max",
            "target_type": "play",
            "target_key": "hamlet",
            "target_label": "Гамлет",
            "at": self.now,
        }
        values.update(overrides)
        campaign_id = create_campaign(self.connection, **values)
        approve_campaign(self.connection, campaign_id, "director", self.now)
        schedule_campaign(
            self.connection, campaign_id, self.due, "admin", self.now
        )
        return campaign_id

    def sender(self, status=200):
        transport = MemoryHttpTransport(status)
        return (
            ChannelReplySender(max_sender=MaxApiSender("fake", transport)),
            transport,
        )

    def test_only_due_scheduled_campaign_is_queued(self):
        self.add_subscriber()
        self.scheduled_campaign()
        self.assertEqual(queue_due_campaigns(
            self.connection, self.protector, now=self.now
        ), 0)
        self.assertEqual(queue_due_campaigns(
            self.connection, self.protector, now=self.due
        ), 1)

    def test_target_preference_is_applied_when_materializing_recipients(self):
        self.add_subscriber("max-1", play_key="hamlet", play_title="Гамлет")
        self.add_subscriber("max-2", play_key="seagull", play_title="Чайка")
        self.scheduled_campaign()
        queued = queue_due_campaigns(
            self.connection, self.protector, now=self.due
        )
        self.assertEqual(queued, 1)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM campaign_recipients"
        ).fetchone()[0], 1)

    def test_success_marks_recipient_sent_and_campaign_completed(self):
        self.add_subscriber()
        campaign_id = self.scheduled_campaign()
        queue_due_campaigns(self.connection, self.protector, now=self.due)
        sender, transport = self.sender()
        report = deliver_persistent_replies(
            self.connection, self.protector, sender, now=self.due
        )
        recipient = self.connection.execute(
            "SELECT status, sent_at FROM campaign_recipients"
        ).fetchone()
        campaign_status = self.connection.execute(
            "SELECT status FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()[0]
        self.assertEqual((report.sent, len(transport.requests)), (1, 1))
        self.assertEqual(recipient["status"], "sent")
        self.assertIsNotNone(recipient["sent_at"])
        self.assertEqual(campaign_status, "completed")

    def test_revoked_marketing_consent_skips_prepared_message(self):
        self.add_subscriber()
        campaign_id = self.scheduled_campaign()
        queue_due_campaigns(self.connection, self.protector, now=self.due)
        record_consent(
            self.connection, self.protector, "max", "max-1",
            "marketing", "v1", False, "test", self.due,
        )
        sender, transport = self.sender()
        report = deliver_persistent_replies(
            self.connection, self.protector, sender, now=self.due
        )
        recipient = self.connection.execute(
            "SELECT status, reason FROM campaign_recipients"
        ).fetchone()
        self.assertEqual((report.cancelled, len(transport.requests)), (1, 0))
        self.assertEqual(
            (recipient["status"], recipient["reason"]),
            ("skipped", "marketing_not_allowed"),
        )
        self.assertEqual(self.connection.execute(
            "SELECT status FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()[0], "completed")

    def test_unsubscribe_skips_prepared_campaign_message(self):
        self.add_subscriber()
        self.scheduled_campaign()
        queue_due_campaigns(self.connection, self.protector, now=self.due)
        unsubscribe(
            self.connection, self.protector, "max", "max-1",
            "v1", "test", self.due,
        )
        sender, transport = self.sender()
        report = deliver_persistent_replies(
            self.connection, self.protector, sender, now=self.due
        )
        self.assertEqual((report.cancelled, len(transport.requests)), (1, 0))

    def test_repeated_queueing_does_not_duplicate_recipient_or_reply(self):
        self.add_subscriber()
        self.scheduled_campaign()
        first = queue_due_campaigns(
            self.connection, self.protector, now=self.due
        )
        second = queue_due_campaigns(
            self.connection, self.protector, now=self.due
        )
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM outgoing_reply_queue"
        ).fetchone()[0], 1)

    def test_three_failures_finish_recipient_with_safe_reason(self):
        self.add_subscriber()
        campaign_id = self.scheduled_campaign()
        queue_due_campaigns(self.connection, self.protector, now=self.due)
        sender, _ = self.sender(503)
        for offset in (0, 5, 15):
            deliver_persistent_replies(
                self.connection,
                self.protector,
                sender,
                now=self.due + timedelta(minutes=offset),
            )
        recipient = self.connection.execute(
            "SELECT status, reason FROM campaign_recipients"
        ).fetchone()
        self.assertEqual(
            (recipient["status"], recipient["reason"]),
            ("failed", "DeliveryError"),
        )
        self.assertEqual(self.connection.execute(
            "SELECT status FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()[0], "completed")

    def test_campaign_without_eligible_recipients_completes(self):
        self.add_subscriber(marketing=False)
        campaign_id = self.scheduled_campaign()
        self.assertEqual(queue_due_campaigns(
            self.connection, self.protector, now=self.due
        ), 0)
        self.assertEqual(self.connection.execute(
            "SELECT status FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()[0], "completed")


if __name__ == "__main__":
    unittest.main()
