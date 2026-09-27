import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.outgoing_delivery import ChannelReplySender, MemoryHttpTransport, MaxApiSender
from theatre_bot.platform_adapters import MaxWebhookAdapter
from theatre_bot.reply_worker import BackgroundReplyWorker
from theatre_bot.webhooks import WebhookRuntime


transport = MemoryHttpTransport()
worker = BackgroundReplyWorker(
    ChannelReplySender(max_sender=MaxApiSender("demo-token", transport)),
    capacity=10,
)
runtime = WebhookRuntime(
    None,
    MaxWebhookAdapter("demo-secret"),
    lambda incoming: OutgoingMessage(
        "max", incoming.external_user_id, Reply("Здравствуйте! Ответ подготовлен ботом.")
    ),
    reply_executor=worker,
)

result = runtime.handle(
    "max",
    {"X-Max-Bot-Api-Secret": "demo-secret"},
    json.dumps({
        "update_type": "message_created",
        "update_id": "demo-background-1",
        "message": {"body": {"text": "Здравствуйте"}},
        "user": {"user_id": 18},
    }).encode("utf-8"),
)
worker.wait_until_idle()
worker.close()
stats = worker.stats()

print(f"Webhook MAX: HTTP {result.status}; ответ платформе: {result.body.decode('utf-8')}")
print(f"Фоновая очередь: принято {stats.accepted}; обработано {stats.sent}; ошибок {stats.failed}")
print(f"Тестовым транспортом подготовлено запросов: {len(transport.requests)}")
print("Внешняя сеть не вызывалась, использованы вымышленные секреты.")
