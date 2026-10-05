import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Card, Reply
from theatre_bot.outgoing_delivery import (
    ChannelReplySender,
    DeliveryError,
    HttpRequest,
    MAX_MESSAGES_URL,
    MemoryHttpTransport,
    UrlLibHttpTransport,
    MaxApiSender,
    VK_MESSAGES_SEND_URL,
    VkApiSender,
)


class OutgoingDeliveryTests(unittest.TestCase):
    def test_real_transport_applies_timeout_and_response_limit(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                self.limit = limit
                return b"ok"

        response = Response()
        with patch("theatre_bot.outgoing_delivery.urlopen", return_value=response) as send:
            result = UrlLibHttpTransport(timeout=7, response_limit=32).send(
                HttpRequest("POST", "https://example.test", {"X-Test": "1"}, b"body")
            )
        self.assertEqual((result.status, result.body), (200, b"ok"))
        self.assertEqual(response.limit, 32)
        self.assertEqual(send.call_args.kwargs["timeout"], 7)

    def test_vk_request_uses_official_method_and_required_fields(self):
        transport = MemoryHttpTransport()
        sender = VkApiSender("fake-vk-token", transport, random_id=lambda: 123)
        sender.send(OutgoingMessage("vk", "17", Reply("Здравствуйте!")))
        request = transport.requests[0]
        fields = parse_qs(request.body.decode("utf-8"))
        self.assertEqual((request.method, request.url), ("POST", VK_MESSAGES_SEND_URL))
        self.assertEqual(fields["peer_id"], ["17"])
        self.assertEqual(fields["random_id"], ["123"])
        self.assertEqual(fields["message"], ["Здравствуйте!"])

    def test_vk_buttons_contain_only_action_and_label(self):
        transport = MemoryHttpTransport()
        sender = VkApiSender("fake-vk-token", transport, random_id=lambda: 1)
        sender.send(OutgoingMessage(
            "vk", "17", Reply("Выберите"),
            (OutgoingButton("subscribe", "Подписаться"),),
        ))
        fields = parse_qs(transport.requests[0].body.decode("utf-8"))
        keyboard = json.loads(fields["keyboard"][0])
        action = keyboard["buttons"][0][0]["action"]
        self.assertEqual(action["label"], "Подписаться")
        self.assertEqual(json.loads(action["payload"]), {"action": "subscribe"})

    def test_vk_renders_reply_cards_as_readable_text(self):
        transport = MemoryHttpTransport()
        sender = VkApiSender("fake-vk-token", transport, random_id=lambda: 1)
        card = Card(
            "Король Лир",
            "09.10.2026 в 18:30",
            "Драма · 16+ · Большая сцена",
            "https://www.cheldrama.ru/plays/king-lear/",
            "42",
        )
        sender.send(OutgoingMessage("vk", "17", Reply("Спектакли 09.10.2026:", (card,))))
        fields = parse_qs(transport.requests[0].body.decode("utf-8"))
        text = fields["message"][0]
        self.assertIn("🎭 Король Лир", text)
        self.assertIn("09.10.2026 в 18:30", text)
        self.assertIn("Подробнее и билеты: https://www.cheldrama.ru/plays/king-lear/", text)

    def test_max_request_uses_current_domain_and_authorization_header(self):
        transport = MemoryHttpTransport()
        sender = MaxApiSender("fake-max-token", transport)
        sender.send(OutgoingMessage("max", "18", Reply("Здравствуйте!")))
        request = transport.requests[0]
        parsed = urlparse(request.url)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", MAX_MESSAGES_URL)
        self.assertEqual(parse_qs(parsed.query), {"user_id": ["18"]})
        self.assertEqual(request.headers["Authorization"], "fake-max-token")
        self.assertNotIn("fake-max-token", request.url)

    def test_max_buttons_use_inline_keyboard_callbacks(self):
        transport = MemoryHttpTransport()
        sender = MaxApiSender("fake-max-token", transport)
        sender.send(OutgoingMessage(
            "max", "18", Reply("Выберите"),
            (OutgoingButton("unsubscribe", "Отписаться"),),
        ))
        body = json.loads(transport.requests[0].body)
        button = body["attachments"][0]["payload"]["buttons"][0][0]
        self.assertEqual(button, {
            "type": "callback", "text": "Отписаться", "payload": "unsubscribe"
        })

    def test_max_renders_reply_cards_as_readable_text(self):
        transport = MemoryHttpTransport()
        sender = MaxApiSender("fake-max-token", transport)
        card = Card(
            "Король Лир",
            "09.10.2026 в 18:30",
            "Драма · 16+",
            "https://www.cheldrama.ru/plays/king-lear/",
            None,
        )
        sender.send(OutgoingMessage("max", "18", Reply("Ближайшие спектакли:", (card,))))
        text = json.loads(transport.requests[0].body)["text"]
        self.assertIn("🎭 Король Лир", text)
        self.assertIn("Подробнее: https://www.cheldrama.ru/plays/king-lear/", text)

    def test_sender_rejects_message_for_other_channel(self):
        with self.assertRaises(ValueError):
            VkApiSender("fake", MemoryHttpTransport()).send(
                OutgoingMessage("max", "18", Reply("Текст"))
            )

    def test_unsuccessful_response_exposes_only_channel_and_status(self):
        sender = MaxApiSender("fake-secret-token", MemoryHttpTransport(503))
        with self.assertRaises(DeliveryError) as raised:
            sender.send(OutgoingMessage("max", "18", Reply("Персональные данные")))
        self.assertEqual((raised.exception.channel, raised.exception.status), ("max", 503))
        self.assertNotIn("fake-secret-token", str(raised.exception))
        self.assertNotIn("Персональные данные", str(raised.exception))

    def test_vk_api_error_inside_http_200_is_not_marked_as_sent(self):
        transport = MemoryHttpTransport(
            200,
            b'{"error":{"error_code":5,"error_msg":"token value"}}',
        )
        sender = VkApiSender("fake-secret-token", transport)
        with self.assertRaises(DeliveryError) as raised:
            sender.send(OutgoingMessage("vk", "17", Reply("Персональные данные")))
        self.assertEqual((raised.exception.channel, raised.exception.status), ("vk", 502))
        self.assertNotIn("fake-secret-token", str(raised.exception))
        self.assertNotIn("token value", str(raised.exception))

    def test_channel_sender_routes_vk_and_max_separately(self):
        vk_transport = MemoryHttpTransport()
        max_transport = MemoryHttpTransport()
        sender = ChannelReplySender(
            VkApiSender("fake-vk", vk_transport, random_id=lambda: 1),
            MaxApiSender("fake-max", max_transport),
        )
        sender.send(OutgoingMessage("vk", "1", Reply("VK")))
        sender.send(OutgoingMessage("max", "2", Reply("MAX")))
        self.assertEqual((len(vk_transport.requests), len(max_transport.requests)), (1, 1))

    def test_unconfigured_channel_cannot_be_sent(self):
        sender = ChannelReplySender()
        with self.assertRaises(RuntimeError):
            sender.send(OutgoingMessage("vk", "1", Reply("Текст")))


if __name__ == "__main__":
    unittest.main()
