from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.subscription_flow import (
    FlowState,
    SubscriptionSession,
    handle_subscription_action,
    legal_documents_reply,
    subscription_menu,
)
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


class SubscriptionFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_subscribers(Path(self.tempdir.name) / "subscribers.sqlite3")
        initialize_subscribers(self.connection)
        self.protector = IdentityProtector(Fernet.generate_key(), b"flow-key" * 4)
        self.session = SubscriptionSession(
            "vk", "vk-demo-1", "Анна", "hamlet", "Гамлет"
        )

    def tearDown(self):
        self.connection.close()
        self.tempdir.cleanup()

    def action_names(self, reply):
        return tuple(button.action for button in reply.buttons)

    def complete_flow(self, marketing: bool = True):
        session = self.session
        for action in (
            "subscribe",
            "accept_personal_data",
            "accept_service",
            "accept_marketing" if marketing else "decline_marketing",
        ):
            session, reply = handle_subscription_action(
                self.connection, self.protector, session, action
            )
        return session, reply

    def test_non_subscriber_is_not_offered_unsubscribe(self):
        reply = subscription_menu(self.connection, self.protector, self.session)
        self.assertIn("subscribe", self.action_names(reply))
        self.assertNotIn("unsubscribe", self.action_names(reply))

    def test_flow_requires_personal_and_service_consents_in_order(self):
        session, reply = handle_subscription_action(
            self.connection, self.protector, self.session, "subscribe"
        )
        self.assertEqual(session.state, FlowState.PERSONAL_DATA)
        session, reply = handle_subscription_action(
            self.connection, self.protector, session, "accept_personal_data"
        )
        self.assertEqual(session.state, FlowState.SERVICE_NOTIFICATIONS)
        session, reply = handle_subscription_action(
            self.connection, self.protector, session, "accept_service"
        )
        self.assertEqual(session.state, FlowState.MARKETING)

    def test_marketing_refusal_does_not_block_service_subscription(self):
        session, reply = self.complete_flow(marketing=False)
        self.assertEqual(session.state, FlowState.ACTIVE)
        self.assertIn("Рекламные сообщения отправляться не будут", reply.text)
        consent = self.connection.execute(
            """
            SELECT action FROM consent_events
            WHERE consent_type = 'marketing' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()[0]
        self.assertEqual(consent, "revoked")

    def test_active_subscriber_is_offered_unsubscribe(self):
        session, _ = self.complete_flow()
        reply = subscription_menu(self.connection, self.protector, session)
        self.assertIn("unsubscribe", self.action_names(reply))
        self.assertNotIn("subscribe", self.action_names(reply))

    def test_unsubscribe_changes_available_action(self):
        session, _ = self.complete_flow()
        session, reply = handle_subscription_action(
            self.connection, self.protector, session, "unsubscribe"
        )
        self.assertEqual(session.state, FlowState.CANCELLED)
        self.assertIn("Подписка отключена", reply.text)
        menu = subscription_menu(self.connection, self.protector, session)
        self.assertEqual(self.action_names(menu), ("subscribe", "legal"))

    def test_active_subscriber_can_choose_only_change_notifications(self):
        session, _ = self.complete_flow()
        session, reply = handle_subscription_action(
            self.connection, self.protector, session, "notify_changes"
        )
        self.assertIn("переносе или отмене", reply.text)
        row = self.connection.execute(
            "SELECT notify_changes, remind_24h FROM subscriptions"
        ).fetchone()
        self.assertEqual(tuple(row), (1, 0))

    def test_unsubscribed_user_can_start_again(self):
        session, _ = self.complete_flow()
        session, _ = handle_subscription_action(
            self.connection, self.protector, session, "unsubscribe"
        )
        session, _ = handle_subscription_action(
            self.connection, self.protector, session, "subscribe"
        )
        self.assertEqual(session.state, FlowState.PERSONAL_DATA)

    def test_legal_documents_are_available_on_request(self):
        reply = legal_documents_reply()
        self.assertIn("Политика обработки персональных данных", reply.text)
        self.assertIn("Согласие на рекламные сообщения", reply.text)


if __name__ == "__main__":
    unittest.main()
