from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.campaigns import (
    approve_campaign,
    create_campaign,
    preview_campaign,
    schedule_campaign,
)
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
    record_consent,
    subscribe_to_play,
)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_subscribers(Path(self.tempdir.name) / "subscribers.sqlite3")
        initialize_subscribers(self.connection)
        self.protector = IdentityProtector(Fernet.generate_key(), b"c" * 32)
        self.now = datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.connection.close()
        self.tempdir.cleanup()

    def add_subscriber(
        self,
        external_id: str,
        name: str,
        channel: str = "vk",
        marketing: bool = True,
        play_key: str = "hamlet",
        play_title: str = "Гамлет",
    ) -> int:
        for consent_type in ("personal_data", "service_notifications"):
            record_consent(
                self.connection, self.protector, channel, external_id,
                consent_type, "v1", True, "channel_bot", self.now,
                display_name=name,
            )
        if marketing:
            record_consent(
                self.connection, self.protector, channel, external_id,
                "marketing", "v1", True, "channel_bot", self.now,
                display_name=name,
            )
        subscribe_to_play(
            self.connection, self.protector, channel, external_id,
            play_key, play_title, self.now,
        )
        return self.connection.execute(
            "SELECT id FROM subscribers WHERE external_id_hash = ?",
            (self.protector.lookup_hash(channel, external_id),),
        ).fetchone()[0]

    def new_campaign(self, **overrides) -> int:
        values = {
            "name": "Предложение любителям Гамлета",
            "message": "Будем рады видеть Вас на спектакле!",
            "created_by": "admin",
            "target_type": "play",
            "target_key": "hamlet",
            "target_label": "Гамлет",
            "at": self.now,
        }
        values.update(overrides)
        return create_campaign(self.connection, **values)

    def test_preview_selects_matching_preference_and_shows_identity(self):
        self.add_subscriber("vk-1", "Анна")
        self.add_subscriber("vk-2", "Борис", play_key="seagull", play_title="Чайка")
        preview = preview_campaign(
            self.connection, self.protector, self.new_campaign(),
            "admin", "audience_check", self.now,
        )
        self.assertEqual(preview.total, 1)
        self.assertEqual(preview.recipients[0].external_id, "vk-1")
        self.assertEqual(preview.recipients[0].display_name, "Анна")

    def test_without_marketing_consent_is_excluded(self):
        self.add_subscriber("vk-1", "Анна", marketing=False)
        preview = preview_campaign(
            self.connection, self.protector, self.new_campaign(),
            "admin", "audience_check", self.now,
        )
        self.assertEqual(preview.total, 0)

    def test_channel_filter_is_applied(self):
        self.add_subscriber("vk-1", "Анна", channel="vk")
        self.add_subscriber("max-1", "Борис", channel="max")
        campaign_id = self.new_campaign(channel="max")
        preview = preview_campaign(
            self.connection, self.protector, campaign_id,
            "admin", "audience_check", self.now,
        )
        self.assertEqual(preview.by_channel, {"max": 1})

    def test_preview_creates_campaign_and_personal_access_logs(self):
        self.add_subscriber("vk-1", "Анна")
        campaign_id = self.new_campaign()
        preview_campaign(
            self.connection, self.protector, campaign_id,
            "admin", "audience_check", self.now,
        )
        events = [row[0] for row in self.connection.execute(
            "SELECT event_type FROM campaign_events ORDER BY id"
        )]
        self.assertEqual(events, ["created", "previewed"])
        self.assertEqual(
            self.connection.execute(
                "SELECT admin_actor FROM subscriber_access_log"
            ).fetchone()[0],
            "admin",
        )

    def test_campaign_requires_approval_before_scheduling(self):
        campaign_id = self.new_campaign()
        with self.assertRaises(ValueError):
            schedule_campaign(
                self.connection, campaign_id, self.now + timedelta(days=1),
                "admin", self.now,
            )
        approve_campaign(self.connection, campaign_id, "director", self.now)
        schedule_campaign(
            self.connection, campaign_id, self.now + timedelta(days=1),
            "admin", self.now,
        )
        row = self.connection.execute(
            "SELECT status, approved_by, scheduled_at FROM campaigns"
        ).fetchone()
        self.assertEqual(row["status"], "scheduled")
        self.assertEqual(row["approved_by"], "director")
        self.assertIsNotNone(row["scheduled_at"])

    def test_campaign_creation_requires_administrator(self):
        with self.assertRaises(PermissionError):
            self.new_campaign(created_by="")

    def test_recent_advertising_recipient_is_excluded(self):
        subscriber_id = self.add_subscriber("vk-1", "Анна")
        old_campaign = self.new_campaign(name="Предыдущая кампания")
        self.connection.execute(
            """
            INSERT INTO campaign_recipients (
                campaign_id, subscriber_id, status, sent_at
            ) VALUES (?, ?, 'sent', ?)
            """,
            (old_campaign, subscriber_id, self.now.isoformat(timespec="seconds")),
        )
        self.connection.commit()
        preview = preview_campaign(
            self.connection, self.protector, self.new_campaign(name="Новая кампания"),
            "admin", "audience_check", self.now,
        )
        self.assertEqual(preview.total, 0)


if __name__ == "__main__":
    unittest.main()
