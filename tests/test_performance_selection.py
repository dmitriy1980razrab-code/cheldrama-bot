from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from logging import Logger
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from theatre_bot.api import create_handler
from theatre_bot.channel_handler import ChannelMessageHandler, SubscriptionFlowStore
from theatre_bot.channels import IncomingMessage
from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.dialog import answer
from theatre_bot.outgoing_delivery import MemoryHttpTransport, VkApiSender, MaxApiSender
from theatre_bot.persistent_replies import enqueue_reply, claim_replies
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


class PerformanceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / 'theatre.sqlite3'
        self.theatre = connect(self.path)
        initialize(self.theatre)
        self.now = datetime(2026, 10, 1, 12)
        items = [AfficheItem(
            title='Гамлет', play_url='https://www.cheldrama.ru/plays/hamlet/',
            starts_at=f'2026-10-{day:02d}T19:00', genre='Драма', age_rating='16+',
            duration=None, venue='Большая сцена', image_url=None,
            ticket_event_id=f'event-{number}',
        ) for number, day in enumerate((10, 12, 15, 20), 1)]
        sync_affiche(self.theatre, items, now=self.now)
        self.subscribers = connect_subscribers(self.root / 'subscribers.sqlite3')
        initialize_subscribers(self.subscribers)
        self.protector = IdentityProtector(Fernet.generate_key(), b's' * 32)
        self.flows = SubscriptionFlowStore()
        self.handler = ChannelMessageHandler(
            self.theatre, self.subscribers, self.protector, flow_store=self.flows,
        )
        self.counter = 0

    def tearDown(self):
        self.theatre.close()
        self.subscribers.close()
        self.directory.cleanup()

    def send(self, text='', action=None, channel='vk', user='user-1', now=None):
        self.counter += 1
        return self.handler.process(
            IncomingMessage(channel, user, str(self.counter), text, 'Test', action),
            self.now if now is None else now,
        )

    def click(self, result, action, channel='vk'):
        button = next(b for b in result.buttons if b.action.endswith(':' + action))
        return self.send(action=button.action, channel=channel)

    def complete(self, key, channel='vk'):
        result = self.send(action='choose:' + key, channel=channel)
        for action in ('subscribe', 'accept_personal_data', 'accept_service', 'decline_marketing'):
            result = self.click(result, action, channel)
        return result

    def test_title_returns_one_link_free_card_and_three_date_choices(self):
        for channel in ('site', 'vk', 'max', 'local'):
            with self.subTest(channel=channel):
                reply = answer(self.theatre, 'Когда ближайший Гамлет?', self.now, channel=channel)
                self.assertEqual(len(reply.cards), 1)
                self.assertEqual(len(reply.choices), 3)
                self.assertFalse(reply.cards[0].play_url)
                self.assertIsNone(reply.cards[0].ticket_event_id)
                self.assertEqual([c.source_key for c in reply.choices],
                                 ['kassy:event-1', 'kassy:event-2', 'kassy:event-3'])
                self.assertNotIn('https://', reply.text)

    def test_fewer_dates_and_no_dates(self):
        self.theatre.execute("UPDATE performances SET status = 'removed' WHERE source_key != 'kassy:event-1'")
        self.theatre.commit()
        reply = answer(self.theatre, 'Гамлет', self.now)
        self.assertEqual(len(reply.choices), 1)
        self.theatre.execute("UPDATE performances SET status = 'removed'")
        self.theatre.commit()
        reply = answer(self.theatre, 'Гамлет', self.now)
        self.assertEqual((reply.cards, reply.choices), ((), ()))

    def test_explicit_date_and_weekday_keep_direct_cards(self):
        for text in ('Что идёт 10 октября?', 'Что идёт в субботу на следующей неделе?',
                     'Гамлет 10 октября', 'Билет на Гамлет 10 октября'):
            with self.subTest(text=text):
                reply = answer(self.theatre, text, self.now)
                self.assertEqual(len(reply.cards), 1)
                self.assertEqual(reply.cards[0].source_key, 'kassy:event-1')
                self.assertTrue(reply.cards[0].play_url)
                self.assertEqual(reply.choices, ())

    def test_play_with_unavailable_date_does_not_fall_back_to_other_dates(self):
        reply = answer(self.theatre, 'Гамлет 11 октября', self.now)
        self.assertEqual((reply.cards, reply.choices), ((), ()))

    def test_selection_is_keyed_not_reparsed_from_button_label(self):
        reply = answer(self.theatre, 'ignored text', self.now,
                       performance_key='kassy:event-2', channel='site')
        self.assertEqual([c.source_key for c in reply.cards], ['kassy:event-2'])
        self.assertIn('12.10.2026', reply.cards[0].subtitle)
        self.assertIn('VK или MAX', reply.text)
        self.assertFalse(reply.choices)

    def test_old_missing_and_removed_performances_are_not_selectable(self):
        for key in ('missing', 'kassy:event-1'):
            reply = answer(self.theatre, 'choose', datetime(2026, 11, 1), performance_key=key)
            self.assertFalse(reply.cards)
        self.theatre.execute("UPDATE performances SET status = 'removed'")
        self.theatre.commit()
        self.assertFalse(self.send(action='choose:kassy:event-1').buttons)

    def test_information_questions_do_not_offer_subscription(self):
        for text in ('Кто режиссёр Гамлет?', 'Кто играет в Гамлет?', 'Сколько длится Гамлет?'):
            with self.subTest(text=text):
                self.assertFalse(self.send(text).buttons)

    def test_date_query_can_subscribe_to_only_its_performance(self):
        result = self.send('Что идёт 12 октября?')
        for action in ('subscribe', 'accept_personal_data', 'accept_service', 'decline_marketing'):
            result = self.click(result, action)
        row = self.subscribers.execute('SELECT performance_key, selected_starts_at FROM subscriptions').fetchone()
        self.assertEqual(tuple(row), ('kassy:event-2', '2026-10-12T19:00'))

    def test_vk_and_max_complete_real_button_path(self):
        for channel in ('vk', 'max'):
            with self.subTest(channel=channel):
                choices = self.send('Гамлет', channel=channel)
                selected = self.send(action=choices.buttons[1].action, channel=channel)
                self.assertIn('12.10.2026', selected.reply.cards[0].subtitle)
                for action in ('subscribe', 'accept_personal_data', 'accept_service', 'decline_marketing'):
                    selected = self.click(selected, action, channel)
        rows = self.subscribers.execute(
            'SELECT s.channel, sub.performance_key FROM subscriptions sub JOIN subscribers s ON s.id = sub.subscriber_id ORDER BY s.channel'
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [('max', 'kassy:event-2'), ('vk', 'kassy:event-2')])

    def test_two_dates_can_be_managed_independently_after_reopening(self):
        self.complete('kassy:event-1')
        self.complete('kassy:event-2')
        reopened = self.send(action='choose:kassy:event-1')
        self.assertTrue(any(b.action.endswith(':configure') for b in reopened.buttons))
        reopened = self.click(reopened, 'configure')
        reopened = self.click(reopened, 'notify_changes')
        self.click(reopened, 'unsubscribe')
        rows = self.subscribers.execute(
            'SELECT performance_key, status, remind_24h FROM subscriptions ORDER BY id'
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ('kassy:event-1', 'unsubscribed', 0), ('kassy:event-2', 'active', 1),
        ])

    def test_stale_consent_button_cannot_apply_to_new_selection(self):
        old = self.send(action='choose:kassy:event-1')
        old = self.click(old, 'subscribe')
        stale = next(b.action for b in old.buttons if b.action.endswith(':accept_personal_data'))
        self.send(action='choose:kassy:event-2')
        result = self.send(action=stale)
        self.assertIn('неактуальна', result.reply.text)
        self.assertEqual(self.subscribers.execute('SELECT count(*) FROM consent_events').fetchone()[0], 0)

    def test_plain_old_subscribe_action_cannot_create_play_wide_subscription(self):
        self.send('Гамлет')
        self.send(action='subscribe')
        self.assertEqual(self.subscribers.execute('SELECT count(*) FROM subscribers').fetchone()[0], 0)

    def test_other_user_cannot_use_subscription_buttons(self):
        result = self.send(action='choose:kassy:event-1')
        stale = next(b.action for b in result.buttons if b.action.endswith(':subscribe'))
        result = self.send(action=stale, user='other')
        self.assertIn('неактуальна', result.reply.text)

    def test_changed_time_between_choice_and_consent_requires_new_selection(self):
        result = self.send(action='choose:kassy:event-1')
        self.theatre.execute("UPDATE performances SET starts_at = '2026-10-10T20:00' WHERE source_key = 'kassy:event-1'")
        self.theatre.commit()
        result = self.click(result, 'subscribe')
        self.assertIn('изменились', result.reply.text)
        self.assertEqual(self.subscribers.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 0)

    def test_expired_session_and_new_text_invalidate_old_subscription_buttons(self):
        for invalidate in ('timeout', 'message'):
            result = self.send(action='choose:kassy:event-1')
            button = next(b.action for b in result.buttons if b.action.endswith(':subscribe'))
            if invalidate == 'message':
                self.send('Кто режиссёр Гамлет?')
                result = self.send(action=button)
            else:
                result = self.send(action=button, now=self.now + timedelta(minutes=31))
            self.assertIn('неактуальна', result.reply.text)

    def test_choice_buttons_survive_persistent_queue_and_sender_rendering(self):
        from urllib.parse import parse_qs
        for channel in ('vk', 'max'):
            result = self.send('Гамлет', channel=channel)
            enqueue_reply(self.subscribers, self.protector, result, channel + '-choices')
            stored = claim_replies(self.subscribers, self.protector, now=self.now + timedelta(days=1000))
            restored = stored[0].message
            self.assertEqual(restored.buttons, result.buttons)
            transport = MemoryHttpTransport()
            sender = VkApiSender('test', transport) if channel == 'vk' else MaxApiSender('test', transport)
            sender.send(restored)
            if channel == 'vk':
                fields = parse_qs(transport.requests[0].body.decode())
                keyboard = json.loads(fields['keyboard'][0])
                action = json.loads(keyboard['buttons'][1][0]['action']['payload'])['action']
                self.assertEqual(action, 'choose:kassy:event-2')
                self.assertNotIn('https://', fields['message'][0])
            else:
                body = json.loads(transport.requests[0].body)
                self.assertEqual(body['attachments'][0]['payload']['buttons'][1][0]['payload'], 'choose:kassy:event-2')

    def test_aware_time_is_converted_to_theatre_time(self):
        self.theatre.execute("UPDATE performances SET starts_at = '2026-10-01T16:00' WHERE source_key = 'kassy:event-1'")
        self.theatre.commit()
        reply = answer(self.theatre, 'choose', datetime(2026, 10, 1, 12, tzinfo=timezone.utc), performance_key='kassy:event-1')
        self.assertFalse(reply.cards)

    def test_http_site_selection_and_invalid_key(self):
        handler = create_handler(self.path, self.root, technical_logger=Logger('test.selection'))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def post(payload):
            request = Request(f'http://127.0.0.1:{server.server_port}/api/chat',
                              data=json.dumps(payload).encode(),
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=3) as response:
                return json.load(response)
        try:
            with patch('theatre_bot.dialog.theatre_now', return_value=self.now):
                first = post({'message': 'Гамлет'})
                self.assertEqual(len(first['cards']), 1)
                self.assertFalse(first['cards'][0]['play_url'])
                self.assertEqual(len(first['choices']), 3)
                selected = post({'message': first['choices'][1]['label'],
                                 'performance_key': first['choices'][1]['source_key'],
                                 'session_id': first['session_id']})
                self.assertEqual(selected['cards'][0]['source_key'], 'kassy:event-2')
                self.assertTrue(selected['cards'][0]['play_url'])
                self.assertFalse(selected['choices'])
                with self.assertRaises(HTTPError) as raised:
                    post({'message': 'choose', 'performance_key': ['bad']})
                self.assertEqual(raised.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


    def test_two_shows_on_same_day_require_one_show_before_subscription(self):
        self.theatre.execute(
            "UPDATE performances SET starts_at = '2026-10-10T21:00' WHERE source_key = 'kassy:event-2'"
        )
        self.theatre.commit()
        result = self.send('Что идёт 10 октября?')
        self.assertEqual(len(result.reply.cards), 2)
        self.assertTrue(all(b.action.startswith('choose:') for b in result.buttons))
        selected = self.send(action=result.buttons[1].action)
        self.assertEqual(len(selected.reply.cards), 1)
        self.assertIn('21:00', selected.reply.cards[0].subtitle)
        self.assertTrue(any(b.action.endswith(':subscribe') for b in selected.buttons))

    def test_legal_back_keeps_selected_performance(self):
        result = self.send(action='choose:kassy:event-2')
        result = self.click(result, 'legal')
        result = self.click(result, 'back')
        self.assertIn('12.10.2026', result.reply.text)
        self.assertTrue(any(b.action.endswith(':subscribe') for b in result.buttons))

    def test_site_derived_key_is_selectable_without_ticket_id(self):
        self.theatre.execute(
            "UPDATE performances SET source_key = 'site:test-hash', ticket_event_id = NULL WHERE source_key = 'kassy:event-1'"
        )
        self.theatre.commit()
        result = self.send('Гамлет')
        self.assertEqual(result.buttons[0].action, 'choose:site:test-hash')
        selected = self.send(action=result.buttons[0].action)
        self.assertIsNone(selected.reply.cards[0].ticket_event_id)
        self.assertTrue(selected.reply.cards[0].play_url)

    def test_server_processor_recreation_keeps_flow_and_bound_buttons(self):
        from theatre_bot.server_webhooks import ServerChannelProcessor
        processor = ServerChannelProcessor(
            self.path, self.root / 'subscribers.sqlite3', self.protector
        )
        with patch('theatre_bot.dialog.theatre_now', return_value=self.now):
            result = processor(IncomingMessage('vk', 'server-user', 'server-choose', '', action='choose:kassy:event-1'))
            for index, action in enumerate(('subscribe', 'accept_personal_data', 'accept_service', 'decline_marketing')):
                button = next(b.action for b in result.buttons if b.action.endswith(':' + action))
                result = processor(IncomingMessage('vk', 'server-user', f'server-{index}', '', action=button))
        row = self.subscribers.execute('SELECT performance_key FROM subscriptions').fetchone()
        self.assertEqual(row[0], 'kassy:event-1')


if __name__ == '__main__':
    unittest.main()
