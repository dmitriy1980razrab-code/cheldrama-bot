from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.site_affiche import AfficheItem


def item(starts_at: str = "2026-09-20T17:00") -> AfficheItem:
    return AfficheItem(
        title="Пять вечеров",
        play_url="https://www.cheldrama.ru/plays/pyat-vecherov/",
        starts_at=starts_at,
        genre="История одной любви",
        age_rating="16+",
        duration="1 час 50 минут",
        venue="Малая сцена",
        image_url="https://www.cheldrama.ru/media/play.jpg",
        ticket_event_id="1067",
    )


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect(Path(self.tempdir.name) / "test.sqlite3")
        initialize(self.connection)

    def tearDown(self):
        self.connection.close()
        self.tempdir.cleanup()

    def test_first_sync_adds_records(self):
        report = sync_affiche(self.connection, [item()])
        self.assertEqual(report.plays_added, 1)
        self.assertEqual(report.performances_added, 1)

    def test_second_sync_does_not_duplicate_records(self):
        sync_affiche(self.connection, [item()])
        report = sync_affiche(self.connection, [item()])
        self.assertEqual(report.plays_added, 0)
        self.assertEqual(report.performances_added, 0)
        self.assertEqual(report.performances_unchanged, 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM plays").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM performances").fetchone()[0], 1)

    def test_changed_time_updates_performance(self):
        sync_affiche(self.connection, [item()])
        report = sync_affiche(self.connection, [item("2026-09-20T18:00")])
        self.assertEqual(report.performances_updated, 1)
        stored = self.connection.execute("SELECT starts_at FROM performances").fetchone()[0]
        self.assertEqual(stored, "2026-09-20T18:00")


if __name__ == "__main__":
    unittest.main()

