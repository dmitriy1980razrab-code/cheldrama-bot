from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sqlite3
import threading

from theatre_bot.channels import IncomingMessage, OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Reply, answer
from theatre_bot.platform_adapters import register_inbound_event
from theatre_bot.queries import find_play_in_text
from theatre_bot.subscribers import IdentityProtector
from theatre_bot.subscription_flow import (
    FlowReply,
    SubscriptionSession,
    handle_subscription_action,
    legal_documents_reply,
    subscription_menu,
)


@dataclass(frozen=True)
class StoredFlow:
    session: SubscriptionSession
    expires_at: datetime


class SubscriptionFlowStore:
    def __init__(self, lifetime: timedelta = timedelta(minutes=30)) -> None:
        self._lifetime = lifetime
        self._items: dict[tuple[str, str], StoredFlow] = {}
        self._lock = threading.Lock()

    def put(
        self,
        session: SubscriptionSession,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        with self._lock:
            self._items[(session.channel, session.external_id)] = StoredFlow(
                session, current + self._lifetime
            )

    def get(
        self,
        channel: str,
        external_id: str,
        now: datetime | None = None,
    ) -> SubscriptionSession | None:
        current = now or datetime.now(timezone.utc)
        key = (channel, external_id)
        with self._lock:
            value = self._items.get(key)
            if value is None or value.expires_at <= current:
                self._items.pop(key, None)
                return None
            return value.session


def _buttons(reply: FlowReply) -> tuple[OutgoingButton, ...]:
    return tuple(OutgoingButton(button.action, button.label) for button in reply.buttons)


class ChannelMessageHandler:
    def __init__(
        self,
        theatre_connection: sqlite3.Connection,
        subscriber_connection: sqlite3.Connection,
        protector: IdentityProtector,
        flow_store: SubscriptionFlowStore | None = None,
    ) -> None:
        self._theatre = theatre_connection
        self._subscribers = subscriber_connection
        self._protector = protector
        self._flows = flow_store or SubscriptionFlowStore()

    def process(
        self,
        message: IncomingMessage,
        now: datetime | None = None,
    ) -> OutgoingMessage | None:
        if message.channel not in {"vk", "max"}:
            raise ValueError("unsupported channel")
        if not register_inbound_event(
            self._subscribers,
            message.channel,
            message.external_message_id,
            at=now,
        ):
            return None

        if message.action == "legal":
            legal = legal_documents_reply()
            return OutgoingMessage(
                message.channel,
                message.external_user_id,
                Reply(legal.text),
                _buttons(legal),
            )

        flow = self._flows.get(
            message.channel, message.external_user_id, now=now
        )
        if message.action:
            if flow is None:
                return OutgoingMessage(
                    message.channel,
                    message.external_user_id,
                    Reply("Сначала выберите спектакль, на уведомления о котором хотите подписаться."),
                )
            updated, flow_reply = handle_subscription_action(
                self._subscribers,
                self._protector,
                flow,
                message.action,
            )
            self._flows.put(updated, now=now)
            return OutgoingMessage(
                message.channel,
                message.external_user_id,
                Reply(flow_reply.text),
                _buttons(flow_reply),
            )

        reply = answer(
            self._theatre,
            message.text,
            now=now,
            channel=message.channel,
        )
        play = find_play_in_text(self._theatre, message.text)
        if play is None:
            return OutgoingMessage(
                message.channel, message.external_user_id, reply
            )

        session = SubscriptionSession(
            channel=message.channel,
            external_id=message.external_user_id,
            display_name=message.display_name,
            play_key=play.source_url,
            play_title=play.title,
        )
        self._flows.put(session, now=now)
        menu = subscription_menu(
            self._subscribers, self._protector, session
        )
        combined = Reply(
            reply.text + "\n\n" + menu.text,
            reply.cards,
        )
        return OutgoingMessage(
            message.channel,
            message.external_user_id,
            combined,
            _buttons(menu),
        )
