from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.dialog import answer
from theatre_bot.site_affiche import AfficheItem


def event(title: str, starts_at: str, event_id: str) -> AfficheItem:
    slug = title.casefold().replace(" ", "-")
    return AfficheItem(
        title=title,
        play_url=f"https://www.cheldrama.ru/plays/{slug}/",
        starts_at=starts_at,
        genre="Драма",
        age_rating="16+",
        duration=None,
        venue="Большая сцена",
        image_url=None,
        ticket_event_id=event_id,
    )


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect(Path(self.tempdir.name) / "test.sqlite3")
        initialize(self.connection)
        sync_affiche(
            self.connection,
            [
                event("Первый спектакль", "2026-09-19T19:00", "1"),
                event("Второй спектакль", "2026-09-20T17:00", "2"),
                event("Третий спектакль", "2026-09-22T18:00", "3"),
                event("Четвёртый спектакль", "2026-09-23T18:00", "4"),
            ],
        )

    def tearDown(self):
        self.connection.close()
        self.tempdir.cleanup()

    def test_nearest_returns_three_cards(self):
        reply = answer(self.connection, "Что идёт?", datetime(2026, 9, 18, 12, 0))
        self.assertEqual(len(reply.cards), 3)
        self.assertEqual(reply.cards[0].title, "Первый спектакль")

    def test_tomorrow_filters_by_date(self):
        reply = answer(self.connection, "Что идёт завтра?", datetime(2026, 9, 18, 12, 0))
        self.assertEqual(len(reply.cards), 1)
        self.assertEqual(reply.cards[0].title, "Первый спектакль")

    def test_weekend_returns_saturday_and_sunday(self):
        reply = answer(self.connection, "Что идёт на выходных?", datetime(2026, 9, 18, 12, 0))
        self.assertEqual(len(reply.cards), 2)

    def test_play_title_returns_its_dates(self):
        reply = answer(self.connection, "Первый спектакль", datetime(2026, 9, 18, 12, 0))
        self.assertEqual(len(reply.cards), 1)
        self.assertEqual(reply.cards[0].title, "Первый спектакль")


if __name__ == "__main__":
    unittest.main()
