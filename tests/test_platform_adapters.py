from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.platform_adapters import (
    MaxWebhookAdapter,
    VkWebhookAdapter,
    register_inbound_event,
)
from theatre_bot.subscribers import connect_subscribers, initialize_subscribers


class PlatformAdapterTests(unittest.TestCase):
    def test_max_requires_official_secret_header(self):
        adapter = MaxWebhookAdapter("max-secret")
        self.assertTrue(adapter.verify_request(
            {"X-Max-Bot-Api-Secret": "max-secret"}, b"{}"
        ))
        self.assertFalse(adapter.verify_request(
            {"X-Max-Bot-Api-Secret": "wrong"}, b"{}"
        ))

    def test_max_parses_message_created(self):
        body = json.dumps({
            "update_type": "message_created",
            "message": {
                "mid": "max-message-1",
                "body": {"text": "Когда Гамлет?"},
                "sender": {"user_id": 42, "name": "Анна"},
            },
        }).encode()
        message = MaxWebhookAdapter("secret").parse_message(body)
        self.assertEqual(message.external_user_id, "42")
        self.assertEqual(message.display_name, "Анна")
        self.assertEqual(message.text, "Когда Гамлет?")

    def test_max_parses_button_callback(self):
        body = json.dumps({
            "update_type": "message_callback",
            "chat_id": 42,
            "callback": {"callback_id": "cb-1", "payload": "subscribe"},
        }).encode()
        message = MaxWebhookAdapter("secret").parse_message(body)
        self.assertEqual(message.action, "subscribe")

    def test_vk_requires_body_secret_and_group(self):
        adapter = VkWebhookAdapter("vk-secret", "100")
        valid = json.dumps({"secret": "vk-secret", "group_id": 100}).encode()
        invalid = json.dumps({"secret": "wrong", "group_id": 100}).encode()
        self.assertTrue(adapter.verify_request({}, valid))
        self.assertFalse(adapter.verify_request({}, invalid))

    def test_vk_parses_message_and_action(self):
        body = json.dumps({
            "type": "message_new",
            "object": {"message": {
                "id": 7, "from_id": 55, "text": "Подписаться",
                "payload": json.dumps({"action": "subscribe"}),
            }},
        }).encode()
        message = VkWebhookAdapter("secret").parse_message(body)
        self.assertEqual(message.external_user_id, "55")
        self.assertEqual(message.action, "subscribe")

    def test_invalid_json_is_rejected(self):
        self.assertIsNone(MaxWebhookAdapter("secret").parse_message(b"not-json"))
        self.assertFalse(VkWebhookAdapter("secret").verify_request({}, b"not-json"))

    def test_duplicate_event_is_processed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = connect_subscribers(Path(directory) / "subscribers.sqlite3")
            initialize_subscribers(connection)
            self.assertTrue(register_inbound_event(connection, "vk", "event-1"))
            self.assertFalse(register_inbound_event(connection, "vk", "event-1"))
            connection.close()


if __name__ == "__main__":
    unittest.main()
