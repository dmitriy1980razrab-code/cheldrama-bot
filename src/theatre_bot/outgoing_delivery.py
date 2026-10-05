from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
from typing import Callable, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from theatre_bot.channels import OutgoingMessage


VK_MESSAGES_SEND_URL = "https://api.vk.com/method/messages.send"
MAX_MESSAGES_URL = "https://platform-api2.max.ru/messages"


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = b""


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse:
        """Выполнить подготовленный HTTP-запрос."""


class MemoryHttpTransport:
    """Тестовый транспорт: сохраняет запросы и никогда не обращается в сеть."""

    def __init__(self, response_status: int = 200, response_body: bytes = b"{}") -> None:
        self.response_status = response_status
        self.response_body = response_body
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(self.response_status, self.response_body)


class UrlLibHttpTransport:
    """Ограниченный HTTPS-транспорт для штатной доставки сообщений."""

    def __init__(self, timeout: float = 15.0, response_limit: int = 65536) -> None:
        if timeout <= 0 or timeout > 120:
            raise ValueError("timeout must be between 0 and 120 seconds")
        if response_limit < 1 or response_limit > 1_048_576:
            raise ValueError("response limit must be between 1 and 1048576 bytes")
        self._timeout = timeout
        self._response_limit = response_limit

    def send(self, request: HttpRequest) -> HttpResponse:
        prepared = Request(
            request.url,
            data=request.body,
            headers=request.headers,
            method=request.method,
        )
        try:
            with urlopen(prepared, timeout=self._timeout) as response:
                return HttpResponse(
                    response.status,
                    response.read(self._response_limit),
                )
        except HTTPError as error:
            return HttpResponse(error.code, error.read(self._response_limit))


class DeliveryError(RuntimeError):
    def __init__(self, channel: str, status: int) -> None:
        self.channel = channel
        self.status = status
        super().__init__(f"{channel} delivery failed with HTTP {status}")


def _require_success(channel: str, response: HttpResponse) -> None:
    if not 200 <= response.status < 300:
        raise DeliveryError(channel, response.status)
    try:
        payload = json.loads(response.body) if response.body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DeliveryError(channel, 502)
    if isinstance(payload, dict) and payload.get("error"):
        raise DeliveryError(channel, 502)


def _vk_keyboard(message: OutgoingMessage) -> str | None:
    if not message.buttons:
        return None
    buttons = [
        [{
            "action": {
                "type": "text",
                "label": button.label,
                "payload": json.dumps(
                    {"action": button.action}, ensure_ascii=False, separators=(",", ":")
                ),
            },
            "color": "primary",
        }]
        for button in message.buttons
    ]
    return json.dumps(
        {"inline": True, "buttons": buttons},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _message_text(message: OutgoingMessage) -> str:
    """Render web-style reply cards as readable text for messenger APIs."""
    sections = [message.reply.text.strip()]
    for card in message.reply.cards:
        lines = [f"🎭 {card.title}"]
        if card.subtitle:
            lines.append(card.subtitle)
        if card.details:
            lines.append(card.details)
        if card.play_url:
            label = "Подробнее и билеты" if card.ticket_event_id else "Подробнее"
            lines.append(f"{label}: {card.play_url}")
        sections.append("\n".join(lines))
    return "\n\n".join(section for section in sections if section)


def _max_attachments(message: OutgoingMessage) -> list[dict]:
    if not message.buttons:
        return []
    return [{
        "type": "inline_keyboard",
        "payload": {
            "buttons": [[{
                "type": "callback",
                "text": button.label,
                "payload": button.action,
            }] for button in message.buttons]
        },
    }]


class VkApiSender:
    def __init__(
        self,
        access_token: str,
        transport: HttpTransport,
        api_version: str = "5.199",
        random_id: Callable[[], int] | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("VK access token is required")
        self._token = access_token
        self._transport = transport
        self._api_version = api_version
        self._random_id = random_id or (lambda: secrets.randbelow(2_147_483_647) + 1)

    def send(self, message: OutgoingMessage) -> None:
        if message.channel != "vk":
            raise ValueError("VK sender accepts only VK messages")
        fields = {
            "access_token": self._token,
            "v": self._api_version,
            "peer_id": message.external_user_id,
            "random_id": str(self._random_id()),
            "message": _message_text(message),
        }
        keyboard = _vk_keyboard(message)
        if keyboard:
            fields["keyboard"] = keyboard
        response = self._transport.send(HttpRequest(
            "POST",
            VK_MESSAGES_SEND_URL,
            {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            urlencode(fields).encode("utf-8"),
        ))
        _require_success("vk", response)


class MaxApiSender:
    def __init__(self, access_token: str, transport: HttpTransport) -> None:
        if not access_token:
            raise ValueError("MAX access token is required")
        self._token = access_token
        self._transport = transport

    def send(self, message: OutgoingMessage) -> None:
        if message.channel != "max":
            raise ValueError("MAX sender accepts only MAX messages")
        body: dict = {"text": _message_text(message)}
        attachments = _max_attachments(message)
        if attachments:
            body["attachments"] = attachments
        response = self._transport.send(HttpRequest(
            "POST",
            f"{MAX_MESSAGES_URL}?{urlencode({'user_id': message.external_user_id})}",
            {
                "Authorization": self._token,
                "Content-Type": "application/json; charset=utf-8",
            },
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ))
        _require_success("max", response)


class ChannelReplySender:
    def __init__(
        self,
        vk_sender: VkApiSender | None = None,
        max_sender: MaxApiSender | None = None,
    ) -> None:
        self._senders = {
            channel: sender
            for channel, sender in {"vk": vk_sender, "max": max_sender}.items()
            if sender is not None
        }

    def send(self, message: OutgoingMessage) -> None:
        sender = self._senders.get(message.channel)
        if sender is None:
            raise RuntimeError(f"{message.channel} outgoing delivery is not configured")
        sender.send(message)
