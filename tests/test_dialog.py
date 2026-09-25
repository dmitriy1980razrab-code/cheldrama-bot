from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, save_play_details, sync_affiche
from theatre_bot.dialog import answer
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.site_play import CastMember, PlayDetails


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
                event("Новогодняя сказка", "2026-12-25T12:00", "5"),
            ],
            now=datetime(2026, 9, 18, 12, 0),
        )
        self.connection.execute(
            "UPDATE plays SET catalog_kind = 'children' WHERE title = 'Новогодняя сказка'"
        )
        self.connection.execute(
            "UPDATE plays SET genre = 'Комедия', age_rating = '6+' WHERE title = 'Второй спектакль'"
        )
        self.connection.execute(
            "UPDATE plays SET duration_minutes = 90 WHERE title = 'Первый спектакль'"
        )
        self.connection.commit()
        first_play_id = self.connection.execute(
            "SELECT id FROM plays WHERE title = 'Первый спектакль'"
        ).fetchone()[0]
        save_play_details(
            self.connection,
            first_play_id,
            PlayDetails(
                title="Первый спектакль",
                director="Режиссёр",
                summary="Аннотация",
                cast=(
                    CastMember(
                        role_name="Главная роль",
                        artist_name="Иван Петров",
                        artist_url="https://www.cheldrama.ru/theatre/people/person/ivan-petrov/",
                    ),
                ),
            ),
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

    def test_artist_surname_returns_repertoire(self):
        reply = answer(self.connection, "Где играет Петров?", datetime(2026, 9, 18, 12, 0))
        self.assertIn("Первый спектакль", reply.text)
        self.assertIn("Главная роль", reply.text)

    def test_artist_nearest_returns_cards_with_role(self):
        reply = answer(
            self.connection,
            "Когда ближайший спектакль с участием Петрова?",
            datetime(2026, 9, 18, 12, 0),
        )
        self.assertEqual(len(reply.cards), 1)
        self.assertIn("Главная роль", reply.cards[0].details)

    def test_weekday_on_next_week(self):
        reply = answer(
            self.connection,
            "Что идёт во вторник на следующей неделе?",
            datetime(2026, 9, 18, 12, 0),
        )
        self.assertEqual(len(reply.cards), 1)
        self.assertEqual(reply.cards[0].title, "Третий спектакль")

    def test_genre_returns_repertoire(self):
        reply = answer(self.connection, "Покажите драмы", datetime(2026, 9, 18, 12, 0))
        self.assertIn("Первый спектакль", reply.text)

    def test_new_year_reply_has_no_cards(self):
        reply = answer(self.connection, "Новогодняя кампания", datetime(2026, 9, 18, 12, 0))
        self.assertIn("Новогодняя сказка", reply.text)
        self.assertEqual(reply.cards, ())

    def test_typo_in_play_title(self):
        reply = answer(self.connection, "Первый спектакал", datetime(2026, 9, 18, 12, 0))
        self.assertEqual(len(reply.cards), 1)
        self.assertEqual(reply.cards[0].title, "Первый спектакль")

    def test_typo_in_artist_surname(self):
        reply = answer(self.connection, "Где играет Питров?", datetime(2026, 9, 18, 12, 0))
        self.assertIn("Иван Петров", reply.text)

    def test_unknown_request_is_logged(self):
        answer(self.connection, "Совершенно неизвестный вопрос", datetime(2026, 9, 18, 12, 0))
        count = self.connection.execute("SELECT count(*) FROM unrecognized_requests").fetchone()[0]
        self.assertEqual(count, 1)

    def test_greeting(self):
        reply = answer(self.connection, "Добрый вечер", datetime(2026, 9, 18, 12, 0))
        self.assertEqual(
            reply.text,
            "Здравствуйте! Разрешите пригласить Вас в мир театра имени Н. Орлова 🎭",
        )

    def test_help(self):
        reply = answer(self.connection, "Что ты умеешь?", datetime(2026, 9, 18, 12, 0))
        self.assertIn("жанре", reply.text)

    def test_template_can_be_edited_in_database(self):
        self.connection.execute(
            "UPDATE response_templates SET template = 'Новый текст' WHERE intent = 'greeting'"
        )
        self.connection.commit()
        reply = answer(self.connection, "Привет", datetime(2026, 9, 18, 12, 0))
        self.assertEqual(reply.text, "Новый текст")

    def test_follow_up_cast_uses_previous_play(self):
        reply = answer(
            self.connection,
            "А кто там играет?",
            datetime(2026, 9, 18, 12, 0),
            history=("Расскажите про Первый спектакль",),
        )
        self.assertIn("Иван Петров", reply.text)

    def test_follow_up_nearest_uses_previous_artist(self):
        reply = answer(
            self.connection,
            "А когда ближайший?",
            datetime(2026, 9, 18, 12, 0),
            history=("Где играет Петров?",),
        )
        self.assertEqual(len(reply.cards), 1)
        self.assertEqual(reply.cards[0].title, "Первый спектакль")

    def test_follow_up_uses_most_recent_entity(self):
        reply = answer(
            self.connection,
            "А когда ближайший?",
            now=datetime(2026, 9, 18, 12, 0),
            history=("Когда идёт Первый спектакль?", "Где играет Иван Петров?"),
        )
        self.assertIn("Иван Петров", reply.text)
        self.assertEqual(len(reply.cards), 1)

    def test_age_returns_only_suitable_performances(self):
        reply = answer(
            self.connection,
            "Что посмотреть ребёнку 10 лет?",
            datetime(2026, 9, 18, 12, 0),
        )
        self.assertEqual([card.title for card in reply.cards], ["Второй спектакль"])

    def test_genre_and_date_are_combined(self):
        reply = answer(
            self.connection,
            "Какая комедия идёт 20.09?",
            datetime(2026, 9, 18, 12, 0),
        )
        self.assertEqual([card.title for card in reply.cards], ["Второй спектакль"])

    def test_play_duration(self):
        reply = answer(
            self.connection,
            "Сколько длится Первый спектакль?",
            datetime(2026, 9, 18, 12, 0),
        )
        self.assertIn("1 ч 30 мин", reply.text)

    def test_play_venue(self):
        reply = answer(
            self.connection,
            "На какой сцене Первый спектакль?",
            datetime(2026, 9, 18, 12, 0),
        )
        self.assertIn("Большая сцена", reply.text)

    def test_ticket_request_returns_official_cards(self):
        reply = answer(
            self.connection,
            "Хочу купить билет",
            datetime(2026, 9, 18, 12, 0),
        )
        self.assertIn("официальной странице", reply.text)
        self.assertEqual(len(reply.cards), 3)

    def test_site_unknown_request_is_not_stored_as_plain_text(self):
        secret_text = "Неизвестный вопрос с личными сведениями"
        answer(
            self.connection,
            secret_text,
            datetime(2026, 9, 18, 12, 0),
            channel="site",
        )
        row = self.connection.execute(
            "SELECT channel, text FROM unrecognized_requests ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["channel"], "site")
        self.assertTrue(row["text"].startswith("fingerprint:"))
        self.assertNotIn(secret_text, row["text"])

    def test_site_play_reply_offers_vk_or_max_notifications(self):
        reply = answer(
            self.connection,
            "Первый спектакль",
            datetime(2026, 9, 18, 12, 0),
            channel="site",
        )
        self.assertIn("VK или MAX", reply.text)
        self.assertIn("за сутки", reply.text)


if __name__ == "__main__":
    unittest.main()
