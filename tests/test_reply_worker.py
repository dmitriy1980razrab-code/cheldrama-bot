import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.outgoing_delivery import (
    ChannelReplySender,
    MemoryHttpTransport,
    MaxApiSender,
    VkApiSender,
)
from theatre_bot.platform_adapters import MaxWebhookAdapter
from theatre_bot.reply_worker import BackgroundReplyWorker
from theatre_bot.webhooks import WebhookRuntime


class ReplyWorkerTests(unittest.TestCase):
    def test_worker_delivers_vk_and_max_through_test_transport(self):
        vk_transport = MemoryHttpTransport()
        max_transport = MemoryHttpTransport()
        sender = ChannelReplySender(
            VkApiSender("fake-vk", vk_transport, random_id=lambda: 1),
            MaxApiSender("fake-max", max_transport),
        )
        worker = BackgroundReplyWorker(sender)
        self.assertTrue(worker.submit(OutgoingMessage("vk", "1", Reply("VK"))))
        self.assertTrue(worker.submit(OutgoingMessage("max", "2", Reply("MAX"))))
        self.assertTrue(worker.wait_until_idle())
        self.assertTrue(worker.close())
        self.assertEqual((len(vk_transport.requests), len(max_transport.requests)), (1, 1))
        self.assertEqual((worker.stats().accepted, worker.stats().sent), (2, 2))

    def test_delivery_failure_is_counted_without_stopping_worker(self):
        transport = MemoryHttpTransport(503)
        worker = BackgroundReplyWorker(
            ChannelReplySender(max_sender=MaxApiSender("fake", transport))
        )
        worker.submit(OutgoingMessage("max", "2", Reply("Секретный текст")))
        self.assertTrue(worker.wait_until_idle())
        worker.close()
        self.assertEqual((worker.stats().failed, worker.stats().sent), (1, 0))

    def test_bounded_queue_rejects_excess_before_start(self):
        worker = BackgroundReplyWorker(ChannelReplySender(), capacity=1, auto_start=False)
        self.assertTrue(worker.submit(OutgoingMessage("vk", "1", Reply("Первый"))))
        self.assertFalse(worker.submit(OutgoingMessage("vk", "2", Reply("Второй"))))
        self.assertEqual(worker.stats().rejected, 1)

    def test_closed_worker_rejects_new_reply(self):
        worker = BackgroundReplyWorker(ChannelReplySender())
        self.assertTrue(worker.close())
        self.assertFalse(worker.submit(OutgoingMessage("vk", "1", Reply("Текст"))))

    def test_webhook_reply_reaches_background_sender(self):
        transport = MemoryHttpTransport()
        worker = BackgroundReplyWorker(
            ChannelReplySender(max_sender=MaxApiSender("fake", transport))
        )
        runtime = WebhookRuntime(
            None,
            MaxWebhookAdapter("max-secret"),
            lambda incoming: OutgoingMessage("max", incoming.external_user_id, Reply("Ответ")),
            reply_executor=worker,
        )
        result = runtime.handle(
            "max",
            {"X-Max-Bot-Api-Secret": "max-secret"},
            json.dumps({
                "update_type": "message_created",
                "update_id": "event-1",
                "message": {"body": {"text": "Вопрос"}},
                "user": {"user_id": 18},
            }).encode("utf-8"),
        )
        self.assertEqual((result.status, result.body), (200, b"ok"))
        self.assertTrue(worker.wait_until_idle())
        worker.close()
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(runtime.take_outgoing(), ())

    def test_full_worker_queue_falls_back_to_webhook_outbox(self):
        worker = BackgroundReplyWorker(ChannelReplySender(), capacity=1, auto_start=False)
        worker.submit(OutgoingMessage("max", "1", Reply("Занято")))
        runtime = WebhookRuntime(
            None,
            MaxWebhookAdapter("max-secret"),
            lambda incoming: OutgoingMessage("max", incoming.external_user_id, Reply("Резерв")),
            reply_executor=worker,
        )
        runtime.handle(
            "max",
            {"X-Max-Bot-Api-Secret": "max-secret"},
            json.dumps({
                "update_type": "message_created",
                "update_id": "event-2",
                "message": {"body": {"text": "Вопрос"}},
                "user": {"user_id": 19},
            }).encode("utf-8"),
        )
        fallback = runtime.take_outgoing()
        self.assertEqual((len(fallback), fallback[0]["chat_id"]), (1, "19"))


if __name__ == "__main__":
    unittest.main()
