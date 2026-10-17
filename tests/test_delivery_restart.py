from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from cryptography.fernet import Fernet
sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from theatre_bot.channels import OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.outgoing_delivery import VkApiSender, HttpResponse
from theatre_bot.persistent_replies import enqueue_reply, claim_replies, deliver_persistent_replies
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


class RestartDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'subscribers.sqlite3'
        self.db = connect_subscribers(self.path)
        initialize_subscribers(self.db)
        self.protector = IdentityProtector(Fernet.generate_key(), b'r' * 32)
        self.now = datetime(2026, 10, 17, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def reopen(self):
        self.db.close()
        self.db = connect_subscribers(self.path)
        initialize_subscribers(self.db)

    def enqueue(self, key='one'):
        enqueue_reply(self.db, self.protector,
                      OutgoingMessage('vk', '123', Reply('Test')), key, self.now)

    def test_identifier_survives_expired_lease_and_reopen(self):
        self.enqueue()
        first = claim_replies(self.db, self.protector, now=self.now)[0]
        self.reopen()
        self.assertEqual(claim_replies(self.db, self.protector, now=self.now), ())
        second = claim_replies(self.db, self.protector,
                               now=self.now + timedelta(minutes=6))[0]
        self.assertEqual(first.message.vk_random_id, second.message.vk_random_id)
        self.assertGreater(first.message.vk_random_id, 0)

    def test_identifier_collision_is_reallocated(self):
        self.enqueue('one')
        self.enqueue('two')
        with patch('theatre_bot.persistent_replies.secrets.randbelow', side_effect=[0, 0, 1]):
            rows = claim_replies(self.db, self.protector, now=self.now)
        self.assertEqual([r.message.vk_random_id for r in rows], [1, 2])

    def test_lost_ack_reuses_identifier_after_restart(self):
        self.enqueue()
        class Platform:
            def __init__(self):
                self.ids = []
                self.received = set()
            def send(self, request):
                fields = parse_qs(request.body.decode())
                identity = (fields['peer_id'][0], fields['random_id'][0])
                self.ids.append(identity)
                self.received.add(identity)
                if len(self.ids) == 1:
                    raise TimeoutError('ack lost after acceptance')
                return HttpResponse(200, b'{"response": 1}')
        platform = Platform()
        sender = VkApiSender('fake', platform)
        self.assertEqual(deliver_persistent_replies(self.db, self.protector, sender, now=self.now).failed, 1)
        self.reopen()
        self.assertEqual(deliver_persistent_replies(self.db, self.protector, sender,
                         now=self.now + timedelta(minutes=6)).sent, 1)
        self.assertEqual(len(platform.ids), 2)
        self.assertEqual(len(platform.received), 1)
        self.reopen()
        self.assertEqual(deliver_persistent_replies(self.db, self.protector, sender,
                         now=self.now + timedelta(days=1)).sent, 0)
        self.assertEqual(len(platform.ids), 2)

    def test_source_write_failure_rolls_back_queue_result(self):
        self.enqueue()
        class Sender:
            def send(self, message):
                pass
        with patch('theatre_bot.persistent_replies._mark_source_result', side_effect=RuntimeError('simulated DB failure')):
            with self.assertRaises(RuntimeError):
                deliver_persistent_replies(self.db, self.protector, Sender(), now=self.now)
        self.reopen()
        row = self.db.execute('SELECT status, sent_at, attempt_count FROM outgoing_reply_queue').fetchone()
        self.assertEqual(tuple(row), ('processing', None, 0))

    def test_second_connection_cannot_claim_live_lease(self):
        self.enqueue()
        self.assertEqual(len(claim_replies(self.db, self.protector, now=self.now)), 1)
        other = connect_subscribers(self.path)
        try:
            self.assertEqual(claim_replies(other, self.protector, now=self.now), ())
        finally:
            other.close()

    def test_migration_keeps_existing_encrypted_queue(self):
        self.enqueue()
        before = bytes(self.db.execute(
            "SELECT payload_encrypted FROM outgoing_reply_queue"
        ).fetchone()[0])
        with self.db:
            self.db.execute("DROP INDEX idx_outgoing_reply_queue_vk_random_id")
            self.db.execute("ALTER TABLE outgoing_reply_queue DROP COLUMN vk_random_id")
        initialize_subscribers(self.db)
        initialize_subscribers(self.db)
        self.assertEqual(bytes(self.db.execute(
            "SELECT payload_encrypted FROM outgoing_reply_queue"
        ).fetchone()[0]), before)
        self.assertIsNotNone(claim_replies(self.db, self.protector,
                                          now=self.now)[0].message.vk_random_id)

    def test_uncertain_reply_never_claimed_even_with_higher_attempt_limit(self):
        self.enqueue()
        with self.db:
            self.db.execute("UPDATE outgoing_reply_queue SET status='failed', failure_reason='delivery_uncertain'")
        self.assertEqual(claim_replies(self.db, self.protector, now=self.now, max_attempts=100), ())

if __name__ == '__main__':
    unittest.main()
