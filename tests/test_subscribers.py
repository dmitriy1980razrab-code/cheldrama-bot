from datetime import datetime, timezone
from pathlib import Path
import os
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.subscribers import (
    IdentityProtector,
    available_subscription_actions,
    connect_subscribers,
    initialize_subscribers,
    list_active_subscriber_profiles,
    record_consent,
    subscriber_profile,
    subscribe_to_play,
    subscriber_stats,
    unsubscribe,
)


class SubscriberTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_subscribers(Path(self.tempdir.name) / "subscribers.sqlite3")
        initialize_subscribers(self.connection)
        self.protector = IdentityProtector(Fernet.generate_key(), b"h" * 32)
        self.at = datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.connection.close()
        self.tempdir.cleanup()

    def grant_required_consents(self, external_id="vk-123", channel="vk"):
        for consent_type in ("personal_data", "service_notifications"):
            record_consent(
                self.connection,
                self.protector,
                channel,
                external_id,
                consent_type,
                "2026-09-25",
                True,
                "channel_bot",
                self.at,
                display_name="Анна Иванова",
            )

    def test_identifier_is_encrypted_and_lookup_is_keyed_hash(self):
        self.grant_required_consents()
        row = self.connection.execute("SELECT * FROM subscribers").fetchone()
        self.assertNotIn(b"vk-123", row["external_id_encrypted"])
        self.assertEqual(self.protector.decrypt(row["external_id_encrypted"]), "vk-123")
        self.assertNotEqual(row["external_id_hash"], "vk-123")
        self.assertNotIn("Анна Иванова".encode("utf-8"), row["display_name_encrypted"])

    def test_subscription_requires_both_consents(self):
        record_consent(
            self.connection, self.protector, "vk", "vk-123", "personal_data",
            "2026-09-25", True, "channel_bot", self.at,
        )
        with self.assertRaises(PermissionError):
            subscribe_to_play(
                self.connection, self.protector, "vk", "vk-123",
                "hamlet", "Гамлет", self.at,
            )

    def test_subscription_records_notifications_time_and_preference(self):
        self.grant_required_consents()
        subscribe_to_play(
            self.connection, self.protector, "vk", "vk-123",
            "hamlet", "Гамлет", self.at,
        )
        subscription = self.connection.execute("SELECT * FROM subscriptions").fetchone()
        subscriber = self.connection.execute("SELECT * FROM subscribers").fetchone()
        preference = self.connection.execute("SELECT * FROM preferences").fetchone()
        self.assertEqual(subscription["notify_changes"], 1)
        self.assertEqual(subscription["remind_24h"], 1)
        self.assertIsNotNone(subscription["subscribed_at"])
        self.assertEqual(subscriber["status"], "active")
        self.assertEqual(preference["preference_label"], "Гамлет")

    def test_unsubscribe_is_only_available_to_active_subscriber(self):
        self.assertEqual(
            available_subscription_actions(self.connection, self.protector, "vk", "vk-123"),
            ("subscribe",),
        )
        self.grant_required_consents()
        subscribe_to_play(
            self.connection, self.protector, "vk", "vk-123",
            "hamlet", "Гамлет", self.at,
        )
        self.assertEqual(
            available_subscription_actions(self.connection, self.protector, "vk", "vk-123"),
            ("configure", "unsubscribe"),
        )

    def test_unsubscribe_clears_delivery_identifier_and_deactivates_topics(self):
        self.grant_required_consents()
        subscribe_to_play(
            self.connection, self.protector, "vk", "vk-123",
            "hamlet", "Гамлет", self.at,
        )
        self.assertTrue(unsubscribe(
            self.connection, self.protector, "vk", "vk-123",
            "2026-09-25", "channel_bot", self.at,
        ))
        subscriber = self.connection.execute("SELECT * FROM subscribers").fetchone()
        topic = self.connection.execute("SELECT * FROM subscriptions").fetchone()
        self.assertEqual(subscriber["status"], "unsubscribed")
        self.assertIsNone(subscriber["external_id_encrypted"])
        self.assertIsNotNone(subscriber["unsubscribed_at"])
        self.assertEqual(topic["status"], "unsubscribed")
        self.assertEqual(
            available_subscription_actions(self.connection, self.protector, "vk", "vk-123"),
            ("subscribe",),
        )

    def test_statistics_are_aggregated_without_identifiers(self):
        self.grant_required_consents()
        subscribe_to_play(
            self.connection, self.protector, "vk", "vk-123",
            "hamlet", "Гамлет", self.at,
        )
        stats = subscriber_stats(self.connection)
        self.assertEqual(stats.active_by_channel, {"vk": 1})
        self.assertEqual(stats.active_play_subscriptions, [("Гамлет", 1)])
        self.assertNotIn("vk-123", repr(stats))

    def test_authorized_admin_can_view_personal_profile_and_preferences(self):
        self.grant_required_consents()
        subscribe_to_play(
            self.connection, self.protector, "vk", "vk-123",
            "hamlet", "Гамлет", self.at,
        )
        subscriber_id = self.connection.execute(
            "SELECT id FROM subscribers"
        ).fetchone()[0]
        profile = subscriber_profile(
            self.connection,
            self.protector,
            subscriber_id,
            "director-account",
            "personal_offer_analysis",
            self.at,
        )
        self.assertEqual(profile.external_id, "vk-123")
        self.assertEqual(profile.display_name, "Анна Иванова")
        self.assertEqual(profile.preferences, (("play", "Гамлет"),))
        log = self.connection.execute("SELECT * FROM subscriber_access_log").fetchone()
        self.assertEqual(log["admin_actor"], "director-account")
        self.assertEqual(log["action"], "view_profile")

    def test_personal_profiles_require_identified_administrator(self):
        self.grant_required_consents()
        with self.assertRaises(PermissionError):
            list_active_subscriber_profiles(
                self.connection, self.protector, "", "analysis", self.at
            )

    def test_active_profile_list_shows_marketing_permission(self):
        self.grant_required_consents()
        record_consent(
            self.connection, self.protector, "vk", "vk-123", "marketing",
            "2026-09-25", True, "channel_bot", self.at,
            display_name="Анна Иванова",
        )
        subscribe_to_play(
            self.connection, self.protector, "vk", "vk-123",
            "hamlet", "Гамлет", self.at,
        )
        profiles = list_active_subscriber_profiles(
            self.connection,
            self.protector,
            "director-account",
            "offer_selection",
            self.at,
        )
        self.assertEqual(len(profiles), 1)
        self.assertTrue(profiles[0].consents["marketing"])
        self.assertEqual(
            self.connection.execute(
                "SELECT action FROM subscriber_access_log"
            ).fetchone()[0],
            "list_profiles",
        )

    def test_keys_can_be_loaded_from_server_secret_files(self):
        encryption_file = Path(self.tempdir.name) / "encryption.key"
        hash_file = Path(self.tempdir.name) / "hash.key"
        encryption_file.write_text(Fernet.generate_key().decode("ascii"), encoding="utf-8")
        hash_file.write_text("s" * 32, encoding="utf-8")
        variables = {
            "THEATRE_SUBSCRIBER_ENCRYPTION_KEY_FILE": str(encryption_file),
            "THEATRE_SUBSCRIBER_HASH_KEY_FILE": str(hash_file),
        }
        previous = {name: os.environ.get(name) for name in variables}
        try:
            os.environ.update(variables)
            loaded = IdentityProtector.from_environment()
            encrypted = loaded.encrypt("vk-777")
            self.assertEqual(loaded.decrypt(encrypted), "vk-777")
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
