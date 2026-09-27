from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import threading
from typing import Callable

from theatre_bot.channels import IncomingMessage, OutgoingMessage
from theatre_bot.platform_adapters import MaxWebhookAdapter, VkWebhookAdapter


@dataclass(frozen=True)
class WebhookResult:
    status: int
    body: bytes


class WebhookRuntime:
    """Проверяет Webhook и готовит ответ, не выполняя внешний API-запрос."""

    def __init__(
        self,
        vk_adapter: VkWebhookAdapter,
        max_adapter: MaxWebhookAdapter,
        processor: Callable[[IncomingMessage], OutgoingMessage | None],
        vk_confirmation_code: str,
        outgoing_limit: int = 1000,
    ) -> None:
        if not vk_confirmation_code:
            raise ValueError("VK confirmation code is required")
        self._adapters = {"vk": vk_adapter, "max": max_adapter}
        self._processor = processor
        self._vk_confirmation_code = vk_confirmation_code
        self._outgoing: deque[dict] = deque(maxlen=max(1, outgoing_limit))
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
                self._outgoing.append(adapter.render_reply(outgoing))
        return WebhookResult(200, b"ok")

    def take_outgoing(self) -> tuple[dict, ...]:
        with self._lock:
            values = tuple(self._outgoing)
            self._outgoing.clear()
        return values
