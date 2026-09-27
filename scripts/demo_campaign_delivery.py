from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.campaign_delivery import queue_due_campaigns
from theatre_bot.campaigns import approve_campaign, create_campaign, schedule_campaign
from theatre_bot.outgoing_delivery import ChannelReplySender, MemoryHttpTransport, MaxApiSender
from theatre_bot.persistent_replies import deliver_persistent_replies
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers, record_consent, subscribe_to_play


now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
due = now + timedelta(hours=1)

with tempfile.TemporaryDirectory(prefix="cheldrama-campaign-delivery-") as directory:
    connection = connect_subscribers(Path(directory) / "subscribers.sqlite3")
    initialize_subscribers(connection)
    protector = IdentityProtector(Fernet.generate_key(), b"campaign-demo-key-" * 2)
    for consent in ("personal_data", "service_notifications", "marketing"):
        record_consent(
            connection, protector, "max", "demo-user", consent,
            "v1", True, "demo", now, display_name="Анна",
        )
    subscribe_to_play(
        connection, protector, "max", "demo-user",
        "hamlet", "Гамлет", now,
    )
    campaign_id = create_campaign(
        connection,
        "Предложение любителям Гамлета",
        "Будем рады видеть Вас на спектакле!",
        "demo-admin",
        channel="max",
        target_type="play",
        target_key="hamlet",
        target_label="Гамлет",
        at=now,
    )
    approve_campaign(connection, campaign_id, "demo-director", now)
    schedule_campaign(connection, campaign_id, due, "demo-admin", now)
    queued = queue_due_campaigns(connection, protector, now=due)
    transport = MemoryHttpTransport()
    delivered = deliver_persistent_replies(
        connection,
        protector,
        ChannelReplySender(max_sender=MaxApiSender("demo-token", transport)),
        now=due,
    )
    recipient = connection.execute(
        "SELECT status FROM campaign_recipients"
    ).fetchone()[0]
    campaign = connection.execute(
        "SELECT status FROM campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()[0]
    connection.close()

print(f"В защищённую очередь добавлено: {queued}")
print(f"Тестовым транспортом обработано: {delivered.sent}")
print(f"Получатель: {recipient}; кампания: {campaign}")
print("Рекламное согласие проверено повторно. Внешняя сеть не вызывалась.")
