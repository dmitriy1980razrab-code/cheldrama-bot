import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.intents import Intent, detect_intent


class IntentTests(unittest.TestCase):
    def test_nearest_schedule(self):
        self.assertEqual(detect_intent("Что идет в ближайшее время?").intent, Intent.SCHEDULE_NEAREST)

    def test_weekend(self):
        self.assertEqual(detect_intent("Что посмотреть на выходных?").intent, Intent.SCHEDULE_WEEKEND)

    def test_cast(self):
        self.assertEqual(detect_intent("Кто играет в спектакле Ревизор?").intent, Intent.PLAY_CAST)

    def test_ticket(self):
        self.assertEqual(detect_intent("Хочу купить билет").intent, Intent.TICKET)

    def test_unknown(self):
        self.assertEqual(detect_intent("Добрый вечер").intent, Intent.UNKNOWN)


if __name__ == "__main__":
    unittest.main()

