from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from theatre_bot.dialog import Reply


@dataclass(frozen=True)
class IncomingMessage:
    channel: str
    external_user_id: str
    external_message_id: str
    text: str


@dataclass(frozen=True)
class OutgoingMessage:
    channel: str
    external_user_id: str
    reply: Reply


class ChannelAdapter(Protocol):
    name: str

    def verify_request(self, headers: dict[str, str], body: bytes) -> bool:
        """Проверить подлинность входящего запроса платформы."""

    def parse_message(self, body: bytes) -> IncomingMessage | None:
        """Преобразовать запрос платформы в единый формат ядра."""

    def render_reply(self, message: OutgoingMessage) -> dict:
        """Преобразовать единый ответ в формат конкретной платформы."""

