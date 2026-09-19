from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.security import ConversationStore, RateLimiter, valid_session_id


class SecurityTests(unittest.TestCase):
    def test_session_id_validation(self):
        self.assertTrue(valid_session_id("12345678-1234-1234-1234-123456789012"))
        self.assertFalse(valid_session_id("short"))
        self.assertFalse(valid_session_id("bad session identifier"))

    def test_rate_limiter(self):
        limiter = RateLimiter(limit=2, window_seconds=60)
        self.assertTrue(limiter.allow("key", now=0))
        self.assertTrue(limiter.allow("key", now=1))
        self.assertFalse(limiter.allow("key", now=2))
        self.assertTrue(limiter.allow("key", now=61))

    def test_conversation_keeps_last_four_turns(self):
        store = ConversationStore(max_turns=4, ttl_seconds=100)
        for number in range(5):
            store.add("session", f"q{number}", f"a{number}", now=number)
        turns = store.get("session", now=5)
        self.assertEqual(len(turns), 4)
        self.assertEqual(turns[0].user_text, "q1")

    def test_conversation_expires(self):
        store = ConversationStore(ttl_seconds=10)
        store.add("session", "q", "a", now=0)
        self.assertEqual(store.get("session", now=11), ())


if __name__ == "__main__":
    unittest.main()

