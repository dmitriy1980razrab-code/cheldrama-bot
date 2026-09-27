from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.outgoing_delivery import (
    ChannelReplySender,
    MemoryHttpTransport,
    MaxApiSender,
)
from theatre_bot.persistent_replies import (
    PersistentReplyExecutor,
    claim_replies,
    deliver_persistent_replies,
    enqueue_reply,
)
from theatre_bot.platform_adapters import MaxWebhookAdapter
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
)
from theatre_bot.webhooks import WebhookRuntime


class PersistentReplyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "subscribers.sqlite3"
        self.connection = connect_subscribers(self.database_path)
        initialize_subscribers(self.connection)
        self.protector = IdentityProtector(Fernet.generate_key(), b"p" * 32)
        self.now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
        self.message = OutgoingMessage(
            "max",
            "user-123",
            Reply("Персональный ответ"),
            (OutgoingButton("subscribe", "Подписаться"),),
        )

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def test_payload_and_user_identifier_are_encrypted(self):
        enqueue_reply(
            self.connection, self.protector, self.message, "event-1", self.now
        )
        row = self.connection.execute(
            "SELECT dedupe_hash, payload_encrypted FROM outgoing_reply_queue"
        ).fetchone()
        self.assertNotIn(b"user-123", row["payload_encrypted"])
        self.assertNotIn("Персональный ответ".encode("utf-8"), row["payload_encrypted"])
        self.assertNotEqual(row["dedupe_hash"], "event-1")

    def test_same_event_is_stored_only_once(self):
        self.assertTrue(enqueue_reply(
            self.connection, self.protector, self.message, "event-1", self.now
        ))
        self.assertFalse(enqueue_reply(
            self.connection, self.protector, self.message, "event-1", self.now
        ))
        count = self.connection.execute(
            "SELECT count(*) FROM outgoing_reply_queue"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_reply_survives_reopen_and_is_delivered_once(self):
        enqueue_reply(
            self.connection, self.protector, self.message, "event-1", self.now
        )
        self.connection.close()
        self.connection = connect_subscribers(self.database_path)
        transport = MemoryHttpTransport()
        sender = ChannelReplySender(max_sender=MaxApiSender("fake", transport))
        report = deliver_persistent_replies(
            self.connection, self.protector, sender, now=self.now
        )
        second = deliver_persistent_replies(
            self.connection, self.protector, sender, now=self.now
        )
        self.assertEqual((report.sent, report.failed), (1, 0))
        self.assertEqual((second.sent, len(transport.requests)), (0, 1))

    def test_failed_reply_waits_before_retry_and_hides_content(self):
        class FailingSender:
            def send(self, message):
                raise RuntimeError("token and personal data")

        enqueue_reply(
            self.connection, self.protector, self.message, "event-1", self.now
        )
        report = deliver_persistent_replies(
            self.connection, self.protector, FailingSender(), now=self.now
        )
        row = self.connection.execute(
            "SELECT status, failure_reason FROM outgoing_reply_queue"
        ).fetchone()
        self.assertEqual((report.sent, report.failed), (0, 1))
        self.assertEqual((row["status"], row["failure_reason"]), ("failed", "RuntimeError"))
        self.assertEqual(
            claim_replies(
                self.connection,
                self.protector,
                now=self.now + timedelta(minutes=4),
            ),
            (),
        )
        self.assertEqual(len(claim_replies(
            self.connection,
            self.protector,
            now=self.now + timedelta(minutes=5),
        )), 1)

    def test_expired_processing_lease_is_recovered(self):
        enqueue_reply(
            self.connection, self.protector, self.message, "event-1", self.now
        )
        self.assertEqual(len(claim_replies(
            self.connection, self.protector, now=self.now
        )), 1)
        self.assertEqual(claim_replies(
            self.connection,
            self.protector,
            now=self.now + timedelta(minutes=4),
        ), ())
        self.assertEqual(len(claim_replies(
            self.connection,
            self.protector,
            now=self.now + timedelta(minutes=6),
        )), 1)

    def test_webhook_persists_reply_before_acknowledgement(self):
        executor = PersistentReplyExecutor(self.database_path, self.protector)
        runtime = WebhookRuntime(
            None,
            MaxWebhookAdapter("max-secret"),
            lambda incoming: OutgoingMessage(
                "max", incoming.external_user_id, Reply("Ответ")
            ),
            reply_executor=executor,
        )
        payload = json.dumps({
            "update_type": "message_created",
            "update_id": "persistent-event-1",
            "message": {"body": {"text": "Вопрос"}},
            "user": {"user_id": 55},
        }).encode("utf-8")
        result = runtime.handle(
            "max", {"X-Max-Bot-Api-Secret": "max-secret"}, payload
        )
        duplicate = runtime.handle(
            "max", {"X-Max-Bot-Api-Secret": "max-secret"}, payload
        )
        self.assertEqual((result.status, duplicate.status), (200, 200))
        count = self.connection.execute(
            "SELECT count(*) FROM outgoing_reply_queue"
        ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(runtime.take_outgoing(), ())

    def test_executor_falls_back_when_dedupe_key_is_missing(self):
        executor = PersistentReplyExecutor(self.database_path, self.protector)
        self.assertFalse(executor.submit(self.message))
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM outgoing_reply_queue"
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
