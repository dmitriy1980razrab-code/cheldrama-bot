from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from cryptography.fernet import Fernet

from theatre_bot.campaigns import (
    approve_campaign,
    create_campaign,
    preview_campaign,
    schedule_campaign,
)
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
    record_consent,
    subscribe_to_play,
    unsubscribe,
)


@dataclass(frozen=True)
class DemoDelivery:
    channel: str
    external_id: str
    message: str
    kind: str


@dataclass(frozen=True)
class DemoReport:
    subscribers_created: int
    campaign_recipients: tuple[str, ...]
    deliveries: tuple[DemoDelivery, ...]
    unsubscribed_name: str
    database_was_temporary: bool


class DemoOutbox:
    """Имитирует VK/MAX и не выполняет сетевые запросы."""

    def __init__(self) -> None:
        self._deliveries: list[DemoDelivery] = []

    def send(self, channel: str, external_id: str, message: str, kind: str) -> None:
        if channel not in {"vk", "max"}:
            raise ValueError("demo supports only VK and MAX")
        self._deliveries.append(DemoDelivery(channel, external_id, message, kind))

    @property
    def deliveries(self) -> tuple[DemoDelivery, ...]:
        return tuple(self._deliveries)


def _add_demo_subscriber(
    connection,
    protector: IdentityProtector,
    channel: str,
    external_id: str,
    name: str,
    play_key: str,
    play_title: str,
    marketing: bool,
    now: datetime,
) -> None:
    for consent_type in ("personal_data", "service_notifications"):
        record_consent(
            connection, protector, channel, external_id, consent_type,
            "demo-v1", True, "demo_channel", now, display_name=name,
        )
    if marketing:
        record_consent(
            connection, protector, channel, external_id, "marketing",
            "demo-v1", True, "demo_channel", now, display_name=name,
        )
    subscribe_to_play(
        connection, protector, channel, external_id,
        play_key, play_title, now,
    )


def run_demo_scenario(now: datetime | None = None) -> DemoReport:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    outbox = DemoOutbox()
    with tempfile.TemporaryDirectory(prefix="cheldrama-demo-") as directory:
        database_path = Path(directory) / "subscribers.sqlite3"
        connection = connect_subscribers(database_path)
        initialize_subscribers(connection)
        protector = IdentityProtector(Fernet.generate_key(), b"demo-hash-key-" * 3)

        _add_demo_subscriber(
            connection, protector, "vk", "demo-vk-anna", "Анна",
            "hamlet", "Гамлет", True, current,
        )
        _add_demo_subscriber(
            connection, protector, "max", "demo-max-boris", "Борис",
            "seagull", "Чайка", True, current,
        )
        _add_demo_subscriber(
            connection, protector, "vk", "demo-vk-maria", "Мария",
            "hamlet", "Гамлет", False, current,
        )

        outbox.send(
            "vk", "demo-vk-anna",
            "Напоминание: спектакль «Гамлет» состоится завтра.",
            "service_reminder",
        )

        campaign_id = create_campaign(
            connection,
            "Предложение зрителям Гамлета",
            "Для Вас доступно специальное предложение на спектакль.",
            "demo-admin",
            target_type="play",
            target_key="hamlet",
            target_label="Гамлет",
            at=current,
        )
        preview = preview_campaign(
            connection, protector, campaign_id,
            "demo-admin", "demo_preview", current,
        )
        approve_campaign(connection, campaign_id, "demo-director", current)
        schedule_campaign(
            connection, campaign_id, current + timedelta(hours=1),
            "demo-admin", current,
        )
        for recipient in preview.recipients:
            outbox.send(
                recipient.channel,
                recipient.external_id,
                "Для Вас доступно специальное предложение на спектакль.",
                "marketing_demo",
            )

        unsubscribe(
            connection, protector, "vk", "demo-vk-maria",
            "demo-v1", "demo_channel", current,
        )
        recipients = tuple(
            recipient.display_name or recipient.external_id
            for recipient in preview.recipients
        )
        subscriber_count = connection.execute(
            "SELECT count(*) FROM subscribers"
        ).fetchone()[0]
        connection.close()
        removed_after_close = not database_path.exists()

    return DemoReport(
        subscribers_created=subscriber_count,
        campaign_recipients=recipients,
        deliveries=outbox.deliveries,
        unsubscribed_name="Мария",
        database_was_temporary=not Path(directory).exists() or removed_after_close,
    )
