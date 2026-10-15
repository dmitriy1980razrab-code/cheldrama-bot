from pathlib import Path
import sqlite3
import sys
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.subscribers import (
    IdentityProtector,
    initialize_subscribers,
    record_consent,
    subscribe_to_play,
)


class PerformanceSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        initialize_subscribers(self.connection)
        self.protector = IdentityProtector(Fernet.generate_key(), b"p" * 32)
        for consent in ("personal_data", "service_notifications"):
            record_consent(
                self.connection, self.protector, "vk", "test-user",
                consent, "test-v1", True, "test",
            )

    def tearDown(self):
        self.connection.close()

    def subscribe(self, key, starts_at):
        return subscribe_to_play(
            self.connection, self.protector, "vk", "test-user",
            "https://example.test/hamlet/", "Hamlet",
            performance_key=key, selected_starts_at=starts_at,
        )

    def test_two_dates_are_separate_with_one_play_preference(self):
        first = self.subscribe("kassy:first", "2026-11-01T18:00")
        second = self.subscribe("kassy:second", "2026-11-02T19:00")
        self.assertNotEqual(first, second)
        rows = self.connection.execute(
            "SELECT performance_key, selected_starts_at FROM subscriptions ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                ("kassy:first", "2026-11-01T18:00"),
                ("kassy:second", "2026-11-02T19:00"),
            ],
        )
        preferences = self.connection.execute(
            "SELECT preference_key FROM preferences"
        ).fetchall()
        self.assertEqual(
            [row[0] for row in preferences], ["https://example.test/hamlet/"]
        )

    def test_repeat_subscription_updates_same_performance(self):
        first = self.subscribe("kassy:first", "2026-11-01T18:00")
        repeated = self.subscribe("kassy:first", "2026-11-01T19:00")
        self.assertEqual(first, repeated)
        rows = self.connection.execute(
            "SELECT selected_starts_at FROM subscriptions"
        ).fetchall()
        self.assertEqual([row[0] for row in rows], ["2026-11-01T19:00"])

    def test_missing_service_consent_prevents_performance_subscription(self):
        record_consent(
            self.connection, self.protector, "vk", "test-user",
            "service_notifications", "test-v1", False, "test",
        )
        with self.assertRaises(PermissionError):
            self.subscribe("kassy:first", "2026-11-01T18:00")
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM subscriptions").fetchone()[0],
            0,
        )

    def test_legacy_subscription_survives_repeated_migration(self):
        schema = (
            Path(__file__).parents[1] / "src/theatre_bot/subscriber_schema.sql"
        ).read_text(encoding="utf-8")
        for column in ("performance_key", "play_key", "selected_starts_at"):
            schema = schema.replace(f"    {column} TEXT,\n", "")
        legacy = sqlite3.connect(":memory:")
        legacy.row_factory = sqlite3.Row
        try:
            legacy.executescript(schema)
            legacy.execute(
                """INSERT INTO subscribers
                   (id, channel, external_id_hash, created_at, updated_at)
                   VALUES (1, 'vk', 'test-hash', 'test-time', 'test-time')"""
            )
            legacy.execute(
                """INSERT INTO subscriptions
                   (id, subscriber_id, topic_type, topic_key, topic_label, subscribed_at)
                   VALUES (7, 1, 'play', 'old-play', 'Old play', 'test-time')"""
            )
            legacy.commit()
            initialize_subscribers(legacy)
            initialize_subscribers(legacy)
            row = legacy.execute(
                "SELECT id, topic_key, status, performance_key FROM subscriptions"
            ).fetchone()
            self.assertEqual(tuple(row), (7, "old-play", "active", None))
            self.assertEqual(list(legacy.execute("PRAGMA foreign_key_check")), [])
        finally:
            legacy.close()


    def test_unsubscribe_keeps_other_date_active(self):
        from theatre_bot.subscribers import (
            performance_subscription,
            unsubscribe_from_performance,
        )
        first = self.subscribe("kassy:first", "2026-11-01T18:00")
        second = self.subscribe("kassy:second", "2026-11-02T19:00")
        self.assertTrue(unsubscribe_from_performance(
            self.connection, self.protector, "vk", "test-user", "kassy:first"
        ))
        rows = self.connection.execute(
            "SELECT id, status FROM subscriptions ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [(first, "unsubscribed"), (second, "active")],
        )
        self.assertIsNone(performance_subscription(
            self.connection, self.protector, "vk", "test-user", "kassy:first"
        ))
        self.assertIsNotNone(performance_subscription(
            self.connection, self.protector, "vk", "test-user", "kassy:second"
        ))
        self.assertFalse(unsubscribe_from_performance(
            self.connection, self.protector, "vk", "test-user", "kassy:first"
        ))

    def test_other_user_cannot_unsubscribe_selected_performance(self):
        from theatre_bot.subscribers import unsubscribe_from_performance
        self.subscribe("kassy:first", "2026-11-01T18:00")
        self.assertFalse(unsubscribe_from_performance(
            self.connection, self.protector, "vk", "other-user", "kassy:first"
        ))
        self.assertEqual(
            self.connection.execute("SELECT status FROM subscriptions").fetchone()[0],
            "active",
        )


if __name__ == "__main__":
    unittest.main()
