from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channel_handler import ConversationContextStore


class ConversationContextTests(unittest.TestCase):
    def setUp(self):
        self.store = ConversationContextStore()
        self.now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)

    def test_keeps_only_last_five_pairs(self):
        for number in range(7):
            self.store.append("vk", "user", str(number), "reply", now=self.now)
        self.assertEqual(
            self.store.get("vk", "user", now=self.now),
            tuple((str(number), "reply") for number in range(2, 7)),
        )

    def test_users_and_channels_are_isolated(self):
        self.store.append("vk", "first", "question", "reply", now=self.now)
        self.assertEqual(self.store.get("vk", "second", now=self.now), ())
        self.assertEqual(self.store.get("max", "first", now=self.now), ())

    def test_expires_after_thirty_minutes(self):
        self.store.append("vk", "user", "question", "reply", now=self.now)
        self.assertTrue(self.store.get(
            "vk", "user", now=self.now + timedelta(minutes=29)
        ))
        self.assertEqual(self.store.get(
            "vk", "user", now=self.now + timedelta(minutes=30)
        ), ())

    def test_new_message_renews_lifetime(self):
        self.store.append("vk", "user", "first", "reply", now=self.now)
        self.store.append(
            "vk", "user", "second", "reply",
            now=self.now + timedelta(minutes=20),
        )
        self.assertEqual(len(self.store.get(
            "vk", "user", now=self.now + timedelta(minutes=40)
        )), 2)

    def test_session_limit_evicts_oldest_context(self):
        store = ConversationContextStore(max_sessions=2)
        for number in range(3):
            store.append(
                "vk", str(number), "question", "reply",
                now=self.now + timedelta(seconds=number),
            )
        self.assertEqual(store.get("vk", "0", now=self.now + timedelta(seconds=3)), ())
        self.assertTrue(store.get("vk", "2", now=self.now + timedelta(seconds=3)))


if __name__ == "__main__":
    unittest.main()
