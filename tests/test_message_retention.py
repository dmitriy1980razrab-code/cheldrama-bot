from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import unittest
from datetime import datetime, timezone
from theatre_bot.database import connect, initialize, log_unrecognized_request, purge_unrecognized_requests
from theatre_bot.subscribers import connect_subscribers, initialize_subscribers
from theatre_bot.persistent_replies import purge_delivered_reply_payloads

class RetentionTests(unittest.TestCase):
    def test_remote_text_is_not_stored(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        initialize(db)
        for channel in ('site', 'vk', 'max'):
            log_unrecognized_request(db, 'личный текст', channel)
        rows = db.execute('SELECT text FROM unrecognized_requests').fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r['text'].startswith('fingerprint:') for r in rows))

    def test_old_diagnostics_removed_and_recent_text_redacted(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        initialize(db)
        with db:
            db.executemany('INSERT INTO unrecognized_requests(channel,text,created_at) VALUES(?,?,?)',
                           [('vk','старое','2026-10-19T00:00:00+00:00'),
                            ('max','новое','2026-10-22T00:00:00+00:00')])
        purge_unrecognized_requests(db, datetime(2026,10,23,tzinfo=timezone.utc))
        rows = db.execute('SELECT text FROM unrecognized_requests').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['text'].startswith('fingerprint:'))

    def test_only_finished_old_payloads_are_cleared(self):
        db = connect_subscribers(':memory:')
        self.addCleanup(db.close)
        initialize_subscribers(db)
        with db:
            for i, status in enumerate(('sent','cancelled','pending','processing','failed')):
                db.execute('''INSERT INTO outgoing_reply_queue
                    (channel,dedupe_hash,payload_encrypted,status,created_at,sent_at)
                    VALUES('vk',?,X'0102',?,'2026-10-01T00:00:00+00:00',?)''',
                    (str(i),status,'2026-10-01T00:00:00+00:00' if status=='sent' else None))
            db.execute('''INSERT INTO outgoing_reply_queue
                (channel,dedupe_hash,payload_encrypted,status,created_at,sent_at)
                VALUES('vk','recent',X'0102','sent','2026-10-01T00:00:00+00:00','2026-10-22T00:00:00+00:00')''')
        now = datetime(2026,10,23,tzinfo=timezone.utc)
        self.assertEqual(purge_delivered_reply_payloads(db, now), 2)
        rows = db.execute('SELECT payload_encrypted FROM outgoing_reply_queue ORDER BY id').fetchall()
        self.assertEqual(len(rows),6)
        self.assertEqual([r[0] for r in rows[:2]], [b'',b''])
        self.assertTrue(all(r[0]==b'\x01\x02' for r in rows[2:]))
        self.assertEqual(purge_delivered_reply_payloads(db, now),0)
