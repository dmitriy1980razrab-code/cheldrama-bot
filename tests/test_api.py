from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.api import serialize_reply
from theatre_bot.dialog import Card, Reply


class ApiTests(unittest.TestCase):
    def test_serializes_reply_and_cards(self):
        reply = Reply(
            text="Ответ",
            cards=(Card("Название", "Дата", "Жанр", "https://example.test", "42"),),
        )
        payload = serialize_reply(reply)
        self.assertEqual(payload["text"], "Ответ")
        self.assertEqual(payload["cards"][0]["ticket_event_id"], "42")


if __name__ == "__main__":
    unittest.main()

