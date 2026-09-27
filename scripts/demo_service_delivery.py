from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.notifications import build_service_notifications
from theatre_bot.outgoing_delivery import ChannelReplySender, MemoryHttpTransport, MaxApiSender
from theatre_bot.persistent_replies import deliver_persistent_replies
from theatre_bot.service_delivery import queue_service_notifications
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers, record_consent, subscribe_to_play


theatre_timezone = timezone(timedelta(hours=5))
now = datetime(2026, 10, 1, 19, 0, tzinfo=theatre_timezone)
delivery_time = now + timedelta(days=1)

with tempfile.TemporaryDirectory(prefix="cheldrama-service-delivery-") as directory:
    root = Path(directory)
    theatre = connect(root / "theatre.sqlite3")
    initialize(theatre)
    subscribers = connect_subscribers(root / "subscribers.sqlite3")
    initialize_subscribers(subscribers)
    protector = IdentityProtector(Fernet.generate_key(), b"service-demo-key-" * 2)
    sync_affiche(theatre, [AfficheItem(
        "Гамлет", "https://www.cheldrama.ru/plays/hamlet/",
        "2026-10-03T19:00", "Драма", "16+", None,
        "Большая сцена", None, "demo-event",
    )], now=now)
    for consent in ("personal_data", "service_notifications"):
        record_consent(
            subscribers, protector, "max", "demo-user", consent,
            "v1", True, "demo", now,
        )
    subscribe_to_play(
        subscribers, protector, "max", "demo-user",
        "https://www.cheldrama.ru/plays/hamlet/", "Гамлет", now,
    )
    built = build_service_notifications(theatre, subscribers, delivery_time)
    queued = queue_service_notifications(
        subscribers, protector, now=delivery_time
    )
    transport = MemoryHttpTransport()
    delivered = deliver_persistent_replies(
        subscribers,
        protector,
        ChannelReplySender(max_sender=MaxApiSender("demo-token", transport)),
        now=delivery_time,
    )
    source_status = subscribers.execute(
        "SELECT status FROM notification_queue"
    ).fetchone()[0]
    theatre.close()
    subscribers.close()

print(f"Напоминаний сформировано: {built.reminders}")
print(f"В защищённую очередь добавлено: {queued}")
print(f"Тестовым транспортом обработано: {delivered.sent}")
print(f"Исходное уведомление: {source_status}")
print("Согласие проверено повторно. Внешняя сеть не вызывалась.")
