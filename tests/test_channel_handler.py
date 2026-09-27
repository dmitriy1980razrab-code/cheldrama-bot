from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channel_handler import ChannelMessageHandler
from theatre_bot.channels import IncomingMessage
from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


class ChannelHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.theatre = connect(root / "theatre.sqlite3")
        initialize(self.theatre)
        sync_affiche(
            self.theatre,
            [AfficheItem(
                title="Гамлет",
                play_url="https://www.cheldrama.ru/plays/hamlet/",
                starts_at="2026-10-10T19:00",
                genre="Драма",
                age_rating="16+",
                duration=None,
                venue="Большая сцена",
                image_url=None,
                ticket_event_id="event-1",
            )],
            now=datetime(2026, 10, 1, 12, 0),
        )
        self.subscribers = connect_subscribers(root / "subscribers.sqlite3")
        initialize_subscribers(self.subscribers)
        self.protector = IdentityProtector(Fernet.generate_key(), b"handler-key" * 4)
        self.handler = ChannelMessageHandler(
            self.theatre, self.subscribers, self.protector
        )
        self.now = datetime(2026, 10, 1, 12, 0)

    def tearDown(self):
        self.theatre.close()
        self.subscribers.close()
        self.tempdir.cleanup()

    def message(self, event_id, text="", action=None, channel="vk", name="Анна"):
        return IncomingMessage(channel, "user-1", event_id, text, name, action)

    def test_regular_message_is_answered_by_dialog_core(self):
        result = self.handler.process(
            self.message("1", "Что идёт?"), self.now
        )
        self.assertEqual(len(result.reply.cards), 1)

    def test_play_message_adds_subscription_button(self):
        result = self.handler.process(
            self.message("2", "Когда идёт Гамлет?"), self.now
        )
        actions = [button.action for button in result.buttons]
        self.assertIn("subscribe", actions)
        self.assertNotIn("unsubscribe", actions)

    def test_button_actions_complete_subscription(self):
        self.handler.process(self.message("3", "Гамлет"), self.now)
        for number, action in enumerate((
            "subscribe", "accept_personal_data", "accept_service", "decline_marketing"
        ), start=4):
            result = self.handler.process(
                self.message(str(number), action=action), self.now
            )
        self.assertIn("Вы подписаны", result.reply.text)
        self.assertEqual(
            self.subscribers.execute(
                "SELECT status FROM subscribers"
            ).fetchone()[0],
            "active",
        )

    def test_duplicate_event_is_ignored(self):
        message = self.message("duplicate", "Что идёт?")
        self.assertIsNotNone(self.handler.process(message, self.now))
        self.assertIsNone(self.handler.process(message, self.now))

    def test_action_without_selected_play_is_safe(self):
        result = self.handler.process(
            self.message("8", action="unsubscribe"), self.now
        )
        self.assertIn("Сначала выберите спектакль", result.reply.text)

    def test_max_name_is_stored_after_consent(self):
        self.handler.process(
            self.message("m1", "Гамлет", channel="max", name="Борис"), self.now
        )
        self.handler.process(
            self.message("m2", action="subscribe", channel="max", name="Борис"), self.now
        )
        self.handler.process(
            self.message("m3", action="accept_personal_data", channel="max", name="Борис"), self.now
        )
        row = self.subscribers.execute(
            "SELECT display_name_encrypted FROM subscribers"
        ).fetchone()
        self.assertEqual(self.protector.decrypt(row[0]), "Борис")


if __name__ == "__main__":
    unittest.main()
