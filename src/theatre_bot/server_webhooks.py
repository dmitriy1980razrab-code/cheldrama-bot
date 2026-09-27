from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from theatre_bot.channel_handler import ChannelMessageHandler, SubscriptionFlowStore
from theatre_bot.channels import IncomingMessage, OutgoingMessage
from theatre_bot.database import connect, initialize
from theatre_bot.platform_adapters import MaxWebhookAdapter, VkWebhookAdapter
from theatre_bot.persistent_replies import PersistentReplyExecutor
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
)
from theatre_bot.webhooks import WebhookRuntime


@dataclass(frozen=True)
class WebhookSettings:
    vk_secret: str | None
    vk_group_id: str | None
    vk_confirmation_code: str | None
    max_secret: str | None

    @classmethod
    def from_environment(cls) -> "WebhookSettings | None":
        values = cls(
            _secret_value("THEATRE_VK_CALLBACK_SECRET"),
            _value("THEATRE_VK_GROUP_ID"),
            _secret_value("THEATRE_VK_CONFIRMATION_CODE"),
            _secret_value("THEATRE_MAX_WEBHOOK_SECRET"),
        )
        vk_values = (
            values.vk_secret,
            values.vk_group_id,
            values.vk_confirmation_code,
        )
        if not any(vk_values) and not values.max_secret:
            return None
        if any(vk_values) and not all(vk_values):
            raise RuntimeError("VK webhook settings are incomplete")
        return values


def _value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _secret_value(name: str) -> str | None:
    secret_file = _value(f"{name}_FILE")
    if secret_file:
        value = Path(secret_file).read_text(encoding="utf-8").strip()
        return value or None
    return _value(name)


class ServerChannelProcessor:
    """Обрабатывает событие в отдельных соединениях с серверными базами."""

    def __init__(
        self,
        theatre_database_path: Path,
        subscriber_database_path: Path,
        protector: IdentityProtector,
    ) -> None:
        self._theatre_path = theatre_database_path
        self._subscriber_path = subscriber_database_path
        self._protector = protector
        self._flows = SubscriptionFlowStore()

    def __call__(self, message: IncomingMessage) -> OutgoingMessage | None:
        theatre = connect(self._theatre_path)
        subscribers = connect_subscribers(self._subscriber_path)
        try:
            initialize(theatre)
            initialize_subscribers(subscribers)
            handler = ChannelMessageHandler(
                theatre,
                subscribers,
                self._protector,
                flow_store=self._flows,
            )
            return handler.process(message)
        finally:
            theatre.close()
            subscribers.close()


def build_webhook_runtime_from_environment(
    theatre_database_path: Path,
    subscriber_database_path: Path,
) -> WebhookRuntime | None:
    settings = WebhookSettings.from_environment()
    if settings is None:
        return None
    protector = IdentityProtector.from_environment()
    processor = ServerChannelProcessor(
        theatre_database_path,
        subscriber_database_path,
        protector,
    )
    vk_adapter = (
        VkWebhookAdapter(settings.vk_secret, settings.vk_group_id)
        if settings.vk_secret
        else None
    )
    max_adapter = MaxWebhookAdapter(settings.max_secret) if settings.max_secret else None
    return WebhookRuntime(
        vk_adapter,
        max_adapter,
        processor,
        settings.vk_confirmation_code,
        reply_executor=PersistentReplyExecutor(
            subscriber_database_path, protector
        ),
    )
