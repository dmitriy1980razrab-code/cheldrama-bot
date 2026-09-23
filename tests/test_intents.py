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
        self.assertEqual(detect_intent("Совершенно неизвестный запрос").intent, Intent.UNKNOWN)

    def test_genre(self):
        self.assertEqual(detect_intent("Покажите комедии").intent, Intent.GENRE)

    def test_weekday(self):
        self.assertEqual(detect_intent("Что идёт в среду?").intent, Intent.SCHEDULE_WEEKDAY)

    def test_new_year_campaign(self):
        self.assertEqual(detect_intent("Новогодняя кампания").intent, Intent.NEW_YEAR)

    def test_typo_in_schedule(self):
        self.assertEqual(detect_intent("Покажи афишу и росписание").intent, Intent.SCHEDULE_NEAREST)

    def test_duration(self):
        self.assertEqual(detect_intent("Сколько длится спектакль?").intent, Intent.PLAY_DURATION)

    def test_venue(self):
        self.assertEqual(detect_intent("На какой сцене спектакль?").intent, Intent.PLAY_VENUE)

    def test_age(self):
        self.assertEqual(detect_intent("Что посмотреть ребёнку 10 лет?").intent, Intent.AGE)


if __name__ == "__main__":
    unittest.main()
