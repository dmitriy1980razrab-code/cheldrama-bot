from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.notifications import build_service_notifications, pending_notifications
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
    record_consent,
    subscribe_to_play,
)


THEATRE_TZ = timezone(timedelta(hours=5))
PLAY_URL = "https://www.cheldrama.ru/plays/demo-hamlet/"


def event(event_id, starts_at):
    return AfficheItem(
        title="Гамлет", play_url=PLAY_URL, starts_at=starts_at,
        genre="Драма", age_rating="16+", duration=None,
        venue="Большая сцена", image_url=None, ticket_event_id=event_id,
    )


with tempfile.TemporaryDirectory(prefix="cheldrama-notifications-") as directory:
    root = Path(directory)
    theatre = connect(root / "theatre.sqlite3")
    initialize(theatre)
    subscribers = connect_subscribers(root / "subscribers.sqlite3")
    initialize_subscribers(subscribers)
    protector = IdentityProtector(Fernet.generate_key(), b"notification-demo-key-" * 2)
    now = datetime(2026, 10, 1, 19, 0, tzinfo=THEATRE_TZ)

    sync_affiche(
        theatre,
        [event("tomorrow", "2026-10-02T19:00"), event("later", "2026-10-05T19:00")],
        now=now,
    )
    for consent_type in ("personal_data", "service_notifications"):
        record_consent(
            subscribers, protector, "vk", "demo-vk-anna", consent_type,
            "demo-v1", True, "demo", now, display_name="Анна",
        )
    subscribe_to_play(
        subscribers, protector, "vk", "demo-vk-anna", PLAY_URL, "Гамлет", now
    )

    first = build_service_notifications(theatre, subscribers, now)
    theatre.execute(
        "UPDATE performances SET starts_at = '2026-10-05T20:00' WHERE source_key = 'kassy:later'"
    )
    theatre.commit()
    second = build_service_notifications(theatre, subscribers, now)
    theatre.execute(
        "UPDATE performances SET status = 'removed' WHERE source_key = 'kassy:later'"
    )
    theatre.commit()
    third = build_service_notifications(theatre, subscribers, now)

    print("Демонстрация очереди сервисных уведомлений")
    print(f"Напоминаний за сутки: {first.reminders}")
    print(f"Сообщений о переносе: {second.rescheduled}")
    print(f"Сообщений о снятии с афиши: {third.removed}")
    print("\nТестовая очередь:")
    for notification in pending_notifications(subscribers, protector):
        print(f"  {notification.channel.upper()} | {notification.notification_type}")
        print(f"  {notification.message}")
    theatre.close()
    subscribers.close()

print("\nВнешние сообщения не отправлялись. Временные базы удалены.")
