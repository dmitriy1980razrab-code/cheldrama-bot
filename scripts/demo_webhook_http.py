from cryptography.fernet import Fernet
from http.server import ThreadingHTTPServer
import json
from logging import Logger
from pathlib import Path
import sys
import tempfile
import threading
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.api import create_handler
from theatre_bot.database import connect, initialize
from theatre_bot.platform_adapters import MaxWebhookAdapter, VkWebhookAdapter
from theatre_bot.server_webhooks import ServerChannelProcessor
from theatre_bot.subscribers import IdentityProtector
from theatre_bot.webhooks import WebhookRuntime


def post(server, path, payload, headers=None):
    request = Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers or {},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        return response.status, response.read().decode("utf-8")


with tempfile.TemporaryDirectory(prefix="cheldrama-webhook-http-") as directory:
    root = Path(directory)
    theatre_path = root / "theatre.sqlite3"
    subscribers_path = root / "subscribers.sqlite3"
    theatre = connect(theatre_path)
    initialize(theatre)
    theatre.close()

    protector = IdentityProtector(
        Fernet.generate_key(), b"webhook-demo-hash-key-0123456789abcdef"
    )
    processor = ServerChannelProcessor(theatre_path, subscribers_path, protector)
    runtime = WebhookRuntime(
        VkWebhookAdapter("demo-vk-secret", "42"),
        MaxWebhookAdapter("demo-max-secret"),
        processor,
        "demo-confirmation",
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(
            theatre_path,
            root,
            technical_logger=Logger("demo.webhook.http"),
            webhook_runtime=runtime,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        vk_status, vk_body = post(
            server,
            "/webhooks/vk",
            {"type": "confirmation", "secret": "demo-vk-secret", "group_id": 42},
        )
        max_status, max_body = post(
            server,
            "/webhooks/max",
            {
                "update_type": "message_created",
                "update_id": "demo-http-1",
                "message": {"body": {"text": "Здравствуйте"}},
                "user": {"user_id": 17, "name": "Анна"},
            },
            {"X-Max-Bot-Api-Secret": "demo-max-secret"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    replies = runtime.take_outgoing()
    print(f"VK confirmation: HTTP {vk_status}; ответ: {vk_body}")
    print(f"MAX message: HTTP {max_status}; ответ: {max_body}")
    print(f"Подготовлено ответов без отправки: {len(replies)}")
    print("Использованы временные базы и вымышленные секреты. Внешняя сеть не вызывалась.")
