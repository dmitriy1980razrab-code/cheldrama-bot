from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import sqlite3

from theatre_bot.channels import IncomingMessage, OutgoingMessage


def register_inbound_event(
    connection: sqlite3.Connection,
    channel: str,
    event_id: str,
    at: datetime | None = None,
) -> bool:
    if channel not in {"vk", "max"} or not event_id:
        raise ValueError("invalid inbound event")
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO inbound_events (channel, event_id, received_at)
            VALUES (?, ?, ?)
            """,
            (channel, event_id, moment.astimezone(timezone.utc).isoformat(timespec="seconds")),
        )
    return cursor.rowcount == 1


def _json(body: bytes) -> dict | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _header(headers: dict[str, str], name: str) -> str:
    expected = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == expected), "")


class VkWebhookAdapter:
    name = "vk"

    def __init__(self, secret: str, group_id: str | None = None) -> None:
        if not secret:
            raise ValueError("VK callback secret is required")
        self._secret = secret
        self._group_id = group_id

    def verify_request(self, headers: dict[str, str], body: bytes) -> bool:
        payload = _json(body)
        if payload is None:
            return False
        if not hmac.compare_digest(str(payload.get("secret", "")), self._secret):
            return False
        return self._group_id is None or str(payload.get("group_id", "")) == self._group_id

    def parse_message(self, body: bytes) -> IncomingMessage | None:
        payload = _json(body)
        if not payload or payload.get("type") != "message_new":
            return None
        message = payload.get("object", {}).get("message", {})
        if not isinstance(message, dict) or "from_id" not in message:
            return None
        raw_payload = message.get("payload")
        action = None
        if isinstance(raw_payload, str):
            parsed_payload = _json(raw_payload.encode("utf-8"))
            action = str(parsed_payload.get("action")) if parsed_payload and parsed_payload.get("action") else None
        event_id = str(message.get("id") or message.get("conversation_message_id") or "")
        if not event_id:
            event_id = hashlib.sha256(body).hexdigest()
        return IncomingMessage(
            channel="vk",
            external_user_id=str(message["from_id"]),
            external_message_id=event_id,
            text=str(message.get("text", "")),
            action=action,
        )

    def render_reply(self, message: OutgoingMessage) -> dict:
        return {
            "peer_id": message.external_user_id,
            "message": message.reply.text,
            "cards": [card.__dict__ for card in message.reply.cards],
        }


class MaxWebhookAdapter:
    name = "max"

    def __init__(self, webhook_secret: str) -> None:
        if not webhook_secret:
            raise ValueError("MAX webhook secret is required")
        self._secret = webhook_secret

    def verify_request(self, headers: dict[str, str], body: bytes) -> bool:
        supplied = _header(headers, "X-Max-Bot-Api-Secret")
        return bool(supplied) and hmac.compare_digest(supplied, self._secret)

    def parse_message(self, body: bytes) -> IncomingMessage | None:
        update = _json(body)
        if not update or update.get("update_type") not in {
            "message_created", "message_callback", "bot_started"
        }:
            return None
        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        user = update.get("user") if isinstance(update.get("user"), dict) else {}
        sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
        identity = user or sender
        user_id = identity.get("user_id") or update.get("chat_id")
        if user_id is None:
            return None
        body_value = message.get("body") if isinstance(message.get("body"), dict) else {}
        callback = update.get("callback") if isinstance(update.get("callback"), dict) else {}
        event_id = str(
            update.get("update_id")
            or message.get("mid")
            or callback.get("callback_id")
            or hashlib.sha256(body).hexdigest()
        )
        display_name = identity.get("name")
        return IncomingMessage(
            channel="max",
            external_user_id=str(user_id),
            external_message_id=event_id,
            text=str(body_value.get("text") or message.get("text") or ""),
            display_name=str(display_name) if display_name else None,
            action=str(callback.get("payload")) if callback.get("payload") else None,
        )

    def render_reply(self, message: OutgoingMessage) -> dict:
        return {
            "chat_id": message.external_user_id,
            "text": message.reply.text,
            "cards": [card.__dict__ for card in message.reply.cards],
        }
