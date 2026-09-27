from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.outgoing_delivery import ChannelReplySender, MemoryHttpTransport, MaxApiSender
from theatre_bot.persistent_delivery_worker import (
    PersistentDeliveryWorker,
    reply_queue_status,
)
from theatre_bot.persistent_replies import PersistentReplyExecutor, enqueue_reply
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


class PersistentDeliveryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "subscribers.sqlite3"
        connection = connect_subscribers(self.database_path)
        initialize_subscribers(connection)
        connection.close()
        self.protector = IdentityProtector(Fernet.generate_key(), b"w" * 32)
        self.transport = MemoryHttpTransport()
        self.sender = ChannelReplySender(
            max_sender=MaxApiSender("fake", self.transport)
        )
        self.message = OutgoingMessage("max", "18", Reply("Ответ"))

    def tearDown(self):
        self.directory.cleanup()

    def test_worker_delivers_reply_queued_before_start(self):
        PersistentReplyExecutor(self.database_path, self.protector).submit(
            self.message, "event-1"
        )
        worker = PersistentDeliveryWorker(
            self.database_path, self.protector, self.sender, poll_interval=0.05
        )
        self.assertTrue(worker.wait_until_idle())
        self.assertTrue(worker.close())
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(worker.status().sent, 1)

    def test_executor_notification_wakes_worker(self):
        worker = PersistentDeliveryWorker(
            self.database_path, self.protector, self.sender, poll_interval=60
        )
        executor = PersistentReplyExecutor(
            self.database_path, self.protector, notify=worker.wake
        )
        executor.submit(self.message, "event-2")
        self.assertTrue(worker.wait_until_idle())
        worker.close()
        self.assertEqual(len(self.transport.requests), 1)

    def test_safe_stop_changes_running_state(self):
        worker = PersistentDeliveryWorker(
            self.database_path, self.protector, self.sender, poll_interval=60
        )
        self.assertTrue(worker.status().running)
        self.assertTrue(worker.close())
        self.assertFalse(worker.status().running)

    def test_queue_status_contains_only_aggregate_counts(self):
        PersistentReplyExecutor(self.database_path, self.protector).submit(
            self.message, "event-3"
        )
        state = reply_queue_status(self.database_path)
        self.assertEqual((state.pending, state.sent), (1, 0))
        self.assertNotIn("18", repr(state))
        self.assertNotIn("Ответ", repr(state))

    def test_failed_delivery_is_counted_and_scheduled(self):
        failing = ChannelReplySender(
            max_sender=MaxApiSender("fake", MemoryHttpTransport(503))
        )
        PersistentReplyExecutor(self.database_path, self.protector).submit(
            self.message, "event-4"
        )
        worker = PersistentDeliveryWorker(
            self.database_path,
            self.protector,
            failing,
            auto_start=False,
        )
        report = worker.run_once(datetime(2026, 9, 27, tzinfo=timezone.utc))
        self.assertEqual((report.sent, report.failed), (0, 1))
        self.assertEqual(worker.status().failed, 1)
        self.assertEqual(reply_queue_status(self.database_path).failed, 1)

    def test_parallel_run_once_does_not_duplicate_delivery(self):
        PersistentReplyExecutor(self.database_path, self.protector).submit(
            self.message, "event-5"
        )
        worker = PersistentDeliveryWorker(
            self.database_path, self.protector, self.sender, auto_start=False
        )
        threads = [threading.Thread(target=worker.run_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(reply_queue_status(self.database_path).sent, 1)

    def test_invalid_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            PersistentDeliveryWorker(
                self.database_path,
                self.protector,
                self.sender,
                poll_interval=0,
                auto_start=False,
            )


if __name__ == "__main__":
    unittest.main()
