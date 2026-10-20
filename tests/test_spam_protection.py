from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from theatre_bot.spam_protection import check_message, SpamPolicy
from theatre_bot.subscribers import (IdentityProtector, connect_subscribers, initialize_subscribers,
    record_consent, subscribe_to_play, performance_subscription)
from theatre_bot.channel_handler import ChannelMessageHandler, SubscriptionFlowStore
from theatre_bot.subscription_flow import SubscriptionSession, FlowState
from theatre_bot.database import connect, initialize
from theatre_bot.channels import IncomingMessage
from theatre_bot.dialog import Reply


class SpamProtectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'subscribers.sqlite3'
        self.db = connect_subscribers(self.path)
        initialize_subscribers(self.db)
        self.protector = IdentityProtector(Fernet.generate_key(), b's' * 32)
        self.now = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()

    def check(self, text='Афиша', at=None, user='123', channel='vk', action=False):
        return check_message(self.db, self.protector, channel, user, text,
                             is_action=action, now=at or self.now)

    def test_text_burst_has_one_warning_then_silence(self):
        for number in range(20):
            self.assertTrue(self.check(str(number)).allowed)
        blocked = self.check('21')
        self.assertEqual((blocked.allowed, blocked.warn), (False, True))
        for number in range(30):
            decision = self.check(str(number))
            self.assertEqual((decision.allowed, decision.warn), (False, False))

    def test_repeated_text_triggers_at_fifth_identical_message(self):
        for _ in range(4):
            self.assertTrue(self.check('Расписание').allowed)
        self.assertTrue(self.check('Расписание').warn)
        self.assertFalse(self.check('Другое').allowed)

    def test_case_spaces_and_unicode_equivalents_are_normalized(self):
        for text in ['АФИША', 'афиша ', '  Афиша', 'афиШа']:
            self.assertTrue(self.check(text).allowed)
        self.assertFalse(self.check('афиша').allowed)

    def test_two_minute_repeat_window_expires(self):
        for _ in range(4):
            self.check()
        self.assertTrue(self.check(at=self.now+timedelta(seconds=120)).allowed)

    def test_typical_flow_button_clicks_do_not_count_as_text_repeats(self):
        for _ in range(15):
            self.assertTrue(self.check('Согласен', action=True).allowed)
        self.assertTrue(self.check('Афиша').allowed)

    def test_action_flood_is_limited_separately(self):
        for _ in range(60):
            self.assertTrue(self.check(action=True).allowed)
        self.assertTrue(self.check(action=True).warn)

    def test_normal_rate_refills_tokens(self):
        for i in range(100):
            self.assertTrue(self.check(str(i), at=self.now+timedelta(seconds=4*i)).allowed)

    def test_pause_expires_without_extension_from_blocked_messages(self):
        for _ in range(5):
            self.check()
        self.assertFalse(self.check(at=self.now+timedelta(minutes=9)).allowed)
        self.assertTrue(self.check(at=self.now+timedelta(minutes=10)).allowed)

    def test_other_users_and_channels_are_independent(self):
        for _ in range(5):
            self.check()
        self.assertTrue(self.check(user='456').allowed)
        self.assertTrue(self.check(channel='max').allowed)

    def test_restart_does_not_reset_pause(self):
        for _ in range(5):
            self.check()
        self.db.close()
        self.db = connect_subscribers(self.path)
        initialize_subscribers(self.db)
        self.assertFalse(self.check().allowed)

    def test_guard_does_not_store_raw_identity_or_message(self):
        self.check('PRIVATE-TEXT-94831', user='PRIVATE-USER-94831')
        row = self.db.execute('SELECT * FROM spam_guard_state').fetchone()
        self.assertNotIn('PRIVATE-TEXT-94831', repr(tuple(row)))
        self.assertNotIn('PRIVATE-USER-94831', repr(tuple(row)))
        self.assertEqual(len(row['actor_hash']), 64)
        self.assertEqual(len(row['repeat_hash']), 64)

    def test_a_link_alone_is_not_blocked(self):
        self.assertTrue(self.check('Где билеты? https://example.org/tickets').allowed)

    def test_old_security_state_is_pruned_on_next_request(self):
        self.check(user='old')
        self.check(user='new', at=self.now+timedelta(days=2))
        self.assertEqual(self.db.execute('SELECT count(*) FROM spam_guard_state').fetchone()[0], 1)

    def test_invalid_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            SpamPolicy(text_limit=0)
        with self.assertRaises(ValueError):
            SpamPolicy(retention_seconds=1)

    def handler(self, flows=None):
        theatre = connect(Path(self.tmp.name) / 'theatre.sqlite3')
        initialize(theatre)
        self.addCleanup(theatre.close)
        return ChannelMessageHandler(theatre, self.db, self.protector, flow_store=flows)

    def message(self, number, text='Афиша', action=None):
        return IncomingMessage('vk', '123', str(number), text, action=action)

    def test_duplicate_event_does_not_increment_counter(self):
        handler = self.handler()
        with patch('theatre_bot.channel_handler.answer', return_value=Reply('ok')):
            first = handler.process(self.message(1), self.now)
            self.assertIsNotNone(first)
            for _ in range(10):
                self.assertIsNone(handler.process(self.message(1), self.now))
        self.assertEqual(self.db.execute('SELECT repeat_count FROM spam_guard_state').fetchone()[0], 1)

    def test_blocked_messages_do_not_reach_dialog_or_context(self):
        handler = self.handler()
        for _ in range(5):
            self.check()
        with patch('theatre_bot.channel_handler.answer') as answer:
            self.assertIsNone(handler.process(self.message('blocked', 'Другой текст'), self.now))
            answer.assert_not_called()
        self.assertEqual(handler._context.get('vk', '123', now=self.now), ())

    def test_long_message_does_not_reach_dialog(self):
        with patch('theatre_bot.channel_handler.answer') as answer:
            result = self.handler().process(self.message('long', 'я'*1001), self.now)
            self.assertIn('1000', result.reply.text)
            answer.assert_not_called()

    def subscribe(self):
        for kind in ('personal_data', 'service_notifications'):
            record_consent(self.db, self.protector, 'vk', '123', kind, 'v1', True, 'test', self.now)
        subscribe_to_play(self.db, self.protector, 'vk', '123', 'play', 'Спектакль', self.now,
                          performance_key='kassy:one', selected_starts_at='2026-10-25T18:00')

    def test_plain_text_unsubscribe_works_during_pause_without_active_flow(self):
        self.subscribe()
        for _ in range(5):
            self.check()
        result = self.handler().process(self.message('opt-out', 'Отписаться'), self.now)
        self.assertIn('Все подписки отключены', result.reply.text)
        self.assertIsNone(performance_subscription(self.db, self.protector, 'vk', '123', 'kassy:one'))
        self.assertIsNone(self.handler().process(self.message('repeat-opt-out', 'Отписаться'), self.now))

    def test_valid_selected_unsubscribe_works_during_pause(self):
        self.subscribe()
        flows = SubscriptionFlowStore()
        # Reopened cards can have OFFER state while the database subscription is active.
        session = SubscriptionSession('vk', '123', None, 'play', 'Спектакль',
            performance_key='kassy:one', selected_starts_at='2026-10-25T18:00', interaction_id='valid')
        flows.put(session, now=self.now)
        for _ in range(5):
            self.check()
        result = self.handler(flows).process(self.message('unsub-button', action='flow:valid:unsubscribe'), self.now)
        self.assertIn('отключена', result.reply.text)
        self.assertIsNone(performance_subscription(self.db, self.protector, 'vk', '123', 'kassy:one'))
        self.assertIsNone(self.handler(flows).process(self.message('repeat', action='flow:valid:unsubscribe'), self.now))

    def test_valid_cancel_works_during_pause_but_cannot_be_replayed_forever(self):
        flows = SubscriptionFlowStore()
        session = SubscriptionSession('vk', '123', None, 'play', 'Спектакль',
            state=FlowState.PERSONAL_DATA, performance_key='kassy:one', interaction_id='valid')
        flows.put(session, now=self.now)
        for _ in range(5):
            self.check()
        handler = self.handler(flows)
        result = handler.process(self.message('cancel', action='flow:valid:cancel'), self.now)
        self.assertIn('отменено', result.reply.text)
        self.assertIsNone(handler.process(self.message('cancel-again', action='flow:valid:cancel'), self.now))

    def test_forged_unsubscribe_button_does_not_bypass_pause(self):
        self.subscribe()
        for _ in range(5):
            self.check()
        result = self.handler().process(self.message('forged', action='flow:fake:unsubscribe'), self.now)
        self.assertIsNone(result)
        self.assertIsNotNone(performance_subscription(self.db, self.protector, 'vk', '123', 'kassy:one'))

    def test_existing_database_adds_security_table_without_altering_subscriptions(self):
        self.subscribe()
        with self.db:
            self.db.execute('DROP TABLE spam_guard_state')
        initialize_subscribers(self.db)
        initialize_subscribers(self.db)
        self.assertTrue(self.check().allowed)
        self.assertIsNotNone(performance_subscription(self.db, self.protector, 'vk', '123', 'kassy:one'))


if __name__ == '__main__':
    unittest.main()
