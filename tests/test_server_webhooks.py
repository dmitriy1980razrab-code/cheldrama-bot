from cryptography.fernet import Fernet
from http.server import ThreadingHTTPServer
import json
from logging import Logger
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.api import create_handler
from theatre_bot.database import connect, initialize
from theatre_bot.server_webhooks import (
    WebhookSettings,
    build_webhook_services_from_environment,
)
from theatre_bot.outgoing_delivery import MemoryHttpTransport


class ServerWebhookTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        theatre = connect(self.root / "theatre.sqlite3")
        initialize(theatre)
        theatre.close()

    def tearDown(self):
        self.directory.cleanup()

    def _environment(self, **extra):
        values = {
            "THEATRE_SUBSCRIBER_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "THEATRE_SUBSCRIBER_HASH_KEY": "server-webhook-hash-key-0123456789abcdef",
        }
        values.update(extra)
        return values

    def _post(self, server, path, payload, headers=None):
        for attempt in range(3):
            request = Request(
                f"http://127.0.0.1:{server.server_port}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers or {},
                method="POST",
            )
            try:
                with urlopen(request, timeout=3) as response:
                    return response.status, response.read()
            except (ConnectionAbortedError, ConnectionResetError):
                if attempt == 2:
                    raise
                threading.Event().wait(0.05)

    def test_server_processor_preserves_only_its_user_context(self):
        from theatre_bot.channels import IncomingMessage
        from theatre_bot.database import sync_affiche
        from theatre_bot.site_affiche import AfficheItem
        from theatre_bot.server_webhooks import ServerChannelProcessor
        from theatre_bot.subscribers import IdentityProtector

        theatre = connect(self.root / "theatre.sqlite3")
        try:
            sync_affiche(theatre, [AfficheItem(
                title="\u0413\u0430\u043c\u043b\u0435\u0442",
                play_url="https://www.cheldrama.ru/plays/hamlet/",
                starts_at="2099-10-10T19:00",
                genre="\u0414\u0440\u0430\u043c\u0430",
                age_rating="16+", duration=None,
                venue="Main", image_url=None, ticket_event_id="context-event",
            )])
        finally:
            theatre.close()
        protector = IdentityProtector(Fernet.generate_key(), b"context-test-key" * 4)
        processor = ServerChannelProcessor(
            self.root / "theatre.sqlite3",
            self.root / "subscribers.sqlite3",
            protector,
        )
        for channel in ("vk", "max"):
            with self.subTest(channel=channel):
                processor(IncomingMessage(
                    channel, "user-1", channel + "-first",
                    "\u0413\u0430\u043c\u043b\u0435\u0442", "Test", None,
                ))
                result = processor(IncomingMessage(
                    channel, "user-1", channel + "-follow",
                    "\u0410 \u043a\u043e\u0433\u0434\u0430?", "Test", None,
                ))
                self.assertEqual(
                    [card.title for card in result.reply.cards],
                    ["\u0413\u0430\u043c\u043b\u0435\u0442"],
                )
                other = processor(IncomingMessage(
                    channel, "user-2", channel + "-other",
                    "\u0410 \u043a\u043e\u0433\u0434\u0430?", "Other", None,
                ))
                self.assertEqual(other.reply.cards, ())
        restarted = ServerChannelProcessor(
            self.root / "theatre.sqlite3",
            self.root / "subscribers.sqlite3",
            protector,
        )
        result = restarted(IncomingMessage(
            "vk", "user-1", "after-restart",
            "\u0410 \u043a\u043e\u0433\u0434\u0430?", "Test", None,
        ))
        self.assertEqual(result.reply.cards, ())

    def test_empty_environment_disables_webhooks(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(WebhookSettings.from_environment())

    def test_incomplete_vk_settings_stop_startup(self):
        with patch.dict(
            os.environ,
            {"THEATRE_VK_CALLBACK_SECRET": "secret"},
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                WebhookSettings.from_environment()

    def test_max_can_be_enabled_independently(self):
        environment = self._environment(
            THEATRE_MAX_WEBHOOK_SECRET="max-secret",
            THEATRE_MAX_ACCESS_TOKEN="max-token",
        )
        with patch.dict(os.environ, environment, clear=True):
            services = build_webhook_services_from_environment(
                self.root / "theatre.sqlite3",
                self.root / "subscribers.sqlite3",
                transport_factory=MemoryHttpTransport,
            )
        self.assertIsNotNone(services)
        self.assertTrue(services.close())

    def test_channel_secret_can_be_loaded_from_server_file(self):
        secret_file = self.root / "max-secret"
        secret_file.write_text("max-from-file\n", encoding="utf-8")
        environment = self._environment(
            THEATRE_MAX_WEBHOOK_SECRET_FILE=str(secret_file),
            THEATRE_MAX_ACCESS_TOKEN="max-token",
        )
        with patch.dict(os.environ, environment, clear=True):
            settings = WebhookSettings.from_environment()
        self.assertEqual(settings.max_secret, "max-from-file")

    def test_vk_delivery_token_is_required_with_webhook(self):
        environment = self._environment(
            THEATRE_VK_CALLBACK_SECRET="vk-secret",
            THEATRE_VK_GROUP_ID="42",
            THEATRE_VK_CONFIRMATION_CODE="confirm-code",
        )
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(RuntimeError):
                WebhookSettings.from_environment()

    def test_vk_can_be_enabled_independently(self):
        environment = self._environment(
            THEATRE_VK_CALLBACK_SECRET="vk-secret",
            THEATRE_VK_GROUP_ID="42",
            THEATRE_VK_CONFIRMATION_CODE="confirm-code",
            THEATRE_VK_ACCESS_TOKEN="vk-token",
        )
        with patch.dict(os.environ, environment, clear=True):
            services = build_webhook_services_from_environment(
                self.root / "theatre.sqlite3",
                self.root / "subscribers.sqlite3",
                transport_factory=MemoryHttpTransport,
            )
        result = services.runtime.handle(
            "vk",
            {},
            json.dumps({
                "type": "confirmation", "secret": "vk-secret", "group_id": 42
            }).encode("utf-8"),
        )
        services.close()
        self.assertEqual((result.status, result.body), (200, b"confirm-code"))

    def test_http_max_event_reaches_dialog_core(self):
        transport = MemoryHttpTransport()
        environment = self._environment(
            THEATRE_MAX_WEBHOOK_SECRET="max-secret",
            THEATRE_MAX_ACCESS_TOKEN="max-token",
        )
        with patch.dict(os.environ, environment, clear=True):
            services = build_webhook_services_from_environment(
                self.root / "theatre.sqlite3",
                self.root / "subscribers.sqlite3",
                transport_factory=lambda: transport,
            )
        handler = create_handler(
            self.root / "theatre.sqlite3",
            self.root,
            technical_logger=Logger("test.http.max"),
            webhook_runtime=services.runtime,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post(
                server,
                "/webhooks/max",
                {
                    "update_type": "message_created",
                    "update_id": "max-http-1",
                    "message": {"body": {"text": "Здравствуйте"}},
                    "user": {"user_id": 17, "name": "Анна"},
                },
                {"X-Max-Bot-Api-Secret": "max-secret"},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertTrue(services.worker.wait_until_idle())
        self.assertTrue(services.close())
        self.assertEqual((status, body), (200, b"ok"))
        subscribers = connect(self.root / "subscribers.sqlite3")
        try:
            queued = subscribers.execute(
                "SELECT channel, status FROM outgoing_reply_queue"
            ).fetchall()
        finally:
            subscribers.close()
        self.assertEqual([(row["channel"], row["status"]) for row in queued], [
            ("max", "sent")
        ])
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(services.runtime.take_outgoing(), ())

    def test_http_webhook_is_hidden_when_disabled(self):
        handler = create_handler(
            self.root / "theatre.sqlite3",
            self.root,
            technical_logger=Logger("test.http.disabled"),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as raised:
                self._post(server, "/webhooks/max", {"update_type": "message_created"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
