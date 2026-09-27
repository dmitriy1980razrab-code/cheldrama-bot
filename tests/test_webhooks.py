import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.platform_adapters import MaxWebhookAdapter, VkWebhookAdapter
from theatre_bot.webhooks import WebhookRuntime


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.received = []

        def processor(message):
            self.received.append(message)
            return OutgoingMessage(
                message.channel,
                message.external_user_id,
                Reply("Ответ бота"),
                (OutgoingButton("subscribe", "Подписаться"),),
            )

        self.runtime = WebhookRuntime(
            VkWebhookAdapter("vk-secret", "100"),
            MaxWebhookAdapter("max-secret"),
            processor,
            "vk-confirmation",
        )

    def test_vk_confirmation_returns_only_confirmation_code(self):
        body = json.dumps({
            "type": "confirmation", "secret": "vk-secret", "group_id": 100
        }).encode()
        result = self.runtime.handle("vk", {}, body)
        self.assertEqual((result.status, result.body), (200, b"vk-confirmation"))
        self.assertEqual(self.received, [])

    def test_invalid_vk_secret_is_forbidden(self):
        body = json.dumps({
            "type": "confirmation", "secret": "wrong", "group_id": 100
        }).encode()
        self.assertEqual(self.runtime.handle("vk", {}, body).status, 403)

    def test_max_message_is_processed_and_acknowledged(self):
        body = json.dumps({
            "update_type": "message_created",
            "message": {
                "mid": "1", "body": {"text": "Гамлет"},
                "sender": {"user_id": 42, "name": "Анна"},
            },
        }).encode()
        result = self.runtime.handle(
            "max", {"X-Max-Bot-Api-Secret": "max-secret"}, body
        )
        self.assertEqual((result.status, result.body), (200, b"ok"))
        self.assertEqual(self.received[0].external_user_id, "42")

    def test_prepared_reply_contains_buttons(self):
        body = json.dumps({
            "update_type": "message_created",
            "message": {
                "mid": "2", "body": {"text": "Гамлет"},
                "sender": {"user_id": 42},
            },
        }).encode()
        self.runtime.handle(
            "max", {"X-Max-Bot-Api-Secret": "max-secret"}, body
        )
        outgoing = self.runtime.take_outgoing()
        self.assertEqual(outgoing[0]["buttons"][0]["action"], "subscribe")

    def test_outgoing_queue_is_drained(self):
        body = json.dumps({
            "update_type": "message_created",
            "message": {"mid": "3", "body": {"text": "Привет"}, "sender": {"user_id": 1}},
        }).encode()
        self.runtime.handle(
            "max", {"X-Max-Bot-Api-Secret": "max-secret"}, body
        )
        self.assertEqual(len(self.runtime.take_outgoing()), 1)
        self.assertEqual(self.runtime.take_outgoing(), ())

    def test_unknown_channel_is_not_found(self):
        self.assertEqual(self.runtime.handle("site", {}, b"{}").status, 404)


if __name__ == "__main__":
    unittest.main()
