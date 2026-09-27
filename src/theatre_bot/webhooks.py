from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import threading
from typing import Callable, Protocol

from theatre_bot.channels import IncomingMessage, OutgoingMessage
from theatre_bot.platform_adapters import MaxWebhookAdapter, VkWebhookAdapter


@dataclass(frozen=True)
class WebhookResult:
    status: int
    body: bytes


class ReplyExecutor(Protocol):
    def submit(
        self, message: OutgoingMessage, dedupe_key: str | None = None
    ) -> bool:
        """Поставить ответ в ограниченную очередь фоновой доставки."""


class WebhookRuntime:
    """Проверяет Webhook и готовит ответ, не выполняя внешний API-запрос."""

    def __init__(
        self,
        vk_adapter: VkWebhookAdapter | None,
        max_adapter: MaxWebhookAdapter | None,
        processor: Callable[[IncomingMessage], OutgoingMessage | None],
        vk_confirmation_code: str | None = None,
        outgoing_limit: int = 1000,
        reply_executor: ReplyExecutor | None = None,
    ) -> None:
        if vk_adapter is None and max_adapter is None:
            raise ValueError("at least one webhook adapter is required")
        if vk_adapter is not None and not vk_confirmation_code:
            raise ValueError("VK confirmation code is required")
        self._adapters = {
            name: adapter
            for name, adapter in {"vk": vk_adapter, "max": max_adapter}.items()
            if adapter is not None
        }
        self._processor = processor
        self._vk_confirmation_code = vk_confirmation_code
        self._outgoing: deque[dict] = deque(maxlen=max(1, outgoing_limit))
        self._reply_executor = reply_executor
        self._lock = threading.Lock()

    def handle(
        self,
        channel: str,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookResult:
        adapter = self._adapters.get(channel)
        if adapter is None:
            return WebhookResult(404, b"not_found")
        if not adapter.verify_request(headers, body):
            return WebhookResult(403, b"forbidden")
        if channel == "vk":
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return WebhookResult(400, b"invalid_json")
            if payload.get("type") == "confirmation":
                return WebhookResult(200, self._vk_confirmation_code.encode("utf-8"))
        message = adapter.parse_message(body)
        if message is None:
            return WebhookResult(200, b"ok")
        with self._lock:
            outgoing = self._processor(message)
            if outgoing is not None:
                queued = (
                    self._reply_executor.submit(
                        outgoing,
                        dedupe_key=f"{message.channel}:{message.external_message_id}",
                    )
                    if self._reply_executor is not None
                    else False
                )
                if not queued:
                    self._outgoing.append(adapter.render_reply(outgoing))
        return WebhookResult(200, b"ok")

    def take_outgoing(self) -> tuple[dict, ...]:
        with self._lock:
            values = tuple(self._outgoing)
            self._outgoing.clear()
        return values
