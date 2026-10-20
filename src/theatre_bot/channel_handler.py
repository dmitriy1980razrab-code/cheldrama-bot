from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import sqlite3
import secrets
import threading

from theatre_bot.channels import IncomingMessage, OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Reply, answer, selected_performance_reply
from theatre_bot.platform_adapters import register_inbound_event
from theatre_bot.subscribers import IdentityProtector, unsubscribe, performance_subscription
from theatre_bot.spam_protection import check_message, normalize_message, MAX_MESSAGE_CHARS
from theatre_bot.subscription_flow import (
    FlowReply,
    FlowState,
    DOCUMENT_VERSIONS,
    SubscriptionSession,
    handle_subscription_action,
    legal_documents_reply,
    subscription_menu,
)


@dataclass(frozen=True)
class StoredConversation:
    pairs: tuple[tuple[str, str], ...]
    expires_at: datetime


class ConversationContextStore:
    def __init__(
        self,
        lifetime: timedelta = timedelta(minutes=30),
        max_sessions: int = 1000,
    ) -> None:
        if lifetime <= timedelta(0) or max_sessions < 1:
            raise ValueError("Context limits must be positive")
        self._lifetime = lifetime
        self._max_sessions = max_sessions
        self._items: dict[tuple[str, str], StoredConversation] = {}
        self._lock = threading.Lock()

    def _current(self, now: datetime | None) -> datetime:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def _prune(self, current: datetime) -> None:
        expired = [
            key for key, value in self._items.items()
            if value.expires_at <= current
        ]
        for key in expired:
            del self._items[key]

    def get(
        self,
        channel: str,
        external_id: str,
        now: datetime | None = None,
    ) -> tuple[tuple[str, str], ...]:
        current = self._current(now)
        with self._lock:
            self._prune(current)
            value = self._items.get((channel, external_id))
            return value.pairs if value is not None else ()

    def append(
        self,
        channel: str,
        external_id: str,
        question: str,
        reply: str,
        now: datetime | None = None,
    ) -> None:
        current = self._current(now)
        key = (channel, external_id)
        with self._lock:
            self._prune(current)
            previous = self._items.pop(key, None)
            pairs = previous.pairs if previous is not None else ()
            pairs = (pairs + ((question[:4000], reply[:8000]),))[-5:]
            self._items[key] = StoredConversation(
                pairs, current + self._lifetime
            )
            while len(self._items) > self._max_sessions:
                del self._items[next(iter(self._items))]


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

    def clear(self, channel: str, external_id: str) -> None:
        with self._lock:
            self._items.pop((channel, external_id), None)



class ChannelMessageHandler:
    def __init__(
        self,
        theatre_connection: sqlite3.Connection,
        subscriber_connection: sqlite3.Connection,
        protector: IdentityProtector,
        flow_store: SubscriptionFlowStore | None = None,
        context_store: ConversationContextStore | None = None,
    ) -> None:
        self._theatre = theatre_connection
        self._subscribers = subscriber_connection
        self._protector = protector
        self._flows = flow_store or SubscriptionFlowStore()
        self._context = context_store if context_store is not None else ConversationContextStore()

    def _flow_buttons(
        self, reply: FlowReply, session: SubscriptionSession,
    ) -> tuple[OutgoingButton, ...]:
        return tuple(
            OutgoingButton(f"flow:{session.interaction_id}:{button.action}", button.label)
            for button in reply.buttons
        )

    def _selected_response(
        self, message: IncomingMessage, reply: Reply, now: datetime | None,
    ) -> OutgoingMessage:
        card = reply.cards[0]
        performance_reply = selected_performance_reply(
            self._theatre, card.source_key, now, message.channel
        )
        if not performance_reply.cards:
            return OutgoingMessage(message.channel, message.external_user_id, reply)
        card = replace(performance_reply.cards[0], details=card.details)
        # The key is authoritative; read its current time rather than parsing display text.
        row = self._theatre.execute(
            "SELECT starts_at FROM performances WHERE source_key = ?", (card.source_key,)
        ).fetchone()
        session = SubscriptionSession(
            channel=message.channel,
            external_id=message.external_user_id,
            display_name=message.display_name,
            play_key=card.play_url,
            play_title=card.title,
            performance_key=card.source_key,
            selected_starts_at=row["starts_at"],
            interaction_id=secrets.token_urlsafe(12),
        )
        self._flows.put(session, now=now)
        menu = subscription_menu(self._subscribers, self._protector, session)
        combined = Reply(reply.text + "\n\n" + menu.text, (card,))
        return OutgoingMessage(
            message.channel, message.external_user_id,
            combined, self._flow_buttons(menu, session),
        )

    def process(
        self,
        message: IncomingMessage,
        now: datetime | None = None,
    ) -> OutgoingMessage | None:
        if message.channel not in {"vk", "max"}:
            raise ValueError("unsupported channel")
        if not register_inbound_event(
            self._subscribers, message.channel, message.external_message_id, at=now,
        ):
            return None

        def response(reply: Reply, buttons=()) -> OutgoingMessage:
            return OutgoingMessage(message.channel, message.external_user_id, reply, buttons)

        opt_out = not message.action and normalize_message(message.text) in {
            "отписаться", "отписаться от всех", "стоп", "stop",
        }
        if opt_out:
            changed = unsubscribe(
                self._subscribers, self._protector, message.channel, message.external_user_id,
                DOCUMENT_VERSIONS["service_notifications"], "channel_bot", at=now,
            )
            if changed:
                self._flows.clear(message.channel, message.external_user_id)
                return response(Reply("Все подписки отключены. Сервисные и рекламные сообщения больше не будут приходить."))

        flow = self._flows.get(message.channel, message.external_user_id, now=now)
        parts = message.action.split(":", 2) if message.action else []
        valid_flow_button = (
            flow is not None and flow.performance_key is not None
            and len(parts) == 3 and parts[0] == "flow" and parts[1] == flow.interaction_id
        )
        # Real unsubscribe/cancel controls remain usable during a temporary pause.
        protected_action = valid_flow_button and (
            (parts[2] == "unsubscribe" and performance_subscription(
                self._subscribers, self._protector, message.channel,
                message.external_user_id, flow.performance_key,
            ) is not None)
            or (parts[2] == "cancel" and flow.state in {
                FlowState.PERSONAL_DATA, FlowState.SERVICE_NOTIFICATIONS, FlowState.MARKETING,
            })
        )
        if not protected_action:
            decision = check_message(
                self._subscribers, self._protector, message.channel, message.external_user_id,
                message.text, is_action=bool(message.action), now=now,
            )
            if not decision.allowed:
                if decision.warn:
                    return response(Reply(
                        "Слишком много сообщений или одинаковых повторов. Сделаем паузу на 10 минут. "
                        "Чтобы отключить все рассылки, напишите «Отписаться»."
                    ))
                return None
        if opt_out:
            return response(Reply("Активных подписок нет."))
        if not message.action and len(message.text) > MAX_MESSAGE_CHARS:
            return response(Reply("Сообщение слишком длинное. Задайте вопрос короче — до 1000 символов."))

        if message.action and message.action.startswith("choose:"):
            key = message.action[len("choose:"):]
            self._flows.clear(message.channel, message.external_user_id)
            if not key or len(key) > 128:
                return response(Reply("Этот показ недоступен. Запросите актуальные даты спектакля."))
            reply = selected_performance_reply(self._theatre, key, now, message.channel)
            if not reply.cards:
                return response(reply)
            self._context.append(
                message.channel, message.external_user_id,
                reply.cards[0].title, reply.text, now=now,
            )
            return self._selected_response(message, reply, now)

        flow = self._flows.get(message.channel, message.external_user_id, now=now)
        if message.action == "legal":
            legal = legal_documents_reply()
            buttons = self._flow_buttons(legal, flow) if flow is not None else ()
            return response(Reply(legal.text), buttons)

        if message.action:
            parts = message.action.split(":", 2)
            if (
                flow is None or flow.performance_key is None
                or len(parts) != 3 or parts[0] != "flow"
                or parts[1] != flow.interaction_id
            ):
                return response(Reply(
                    "Сначала выберите спектакль и показ заново: эта кнопка уже неактуальна."
                ))
            action = parts[2]
            if action in {"subscribe", "accept_personal_data", "accept_service",
                          "accept_marketing", "decline_marketing"}:
                current = selected_performance_reply(self._theatre, flow.performance_key, now)
                row = self._theatre.execute(
                    "SELECT starts_at FROM performances WHERE source_key = ?",
                    (flow.performance_key,),
                ).fetchone()
                if not current.cards or row["starts_at"] != flow.selected_starts_at:
                    self._flows.clear(message.channel, message.external_user_id)
                    return response(Reply(
                        "Дата или доступность показа изменились. Выберите показ заново."
                    ))
            updated, flow_reply = handle_subscription_action(
                self._subscribers, self._protector, flow, action,
            )
            self._flows.put(updated, now=now)
            return response(Reply(flow_reply.text), self._flow_buttons(flow_reply, updated))

        self._flows.clear(message.channel, message.external_user_id)
        pairs = self._context.get(message.channel, message.external_user_id, now=now)
        reply = answer(
            self._theatre, message.text, now=now, channel=message.channel,
            history=tuple(question for question, _ in pairs),
        )
        self._context.append(
            message.channel, message.external_user_id,
            message.text, reply.text, now=now,
        )
        if reply.choices:
            return response(
                Reply(reply.text, reply.cards),
                tuple(OutgoingButton(f"choose:{choice.source_key}", choice.label)
                      for choice in reply.choices),
            )
        if len(reply.cards) == 1 and reply.cards[0].source_key:
            return self._selected_response(message, reply, now)
        if len(reply.cards) > 1:
            buttons = tuple(
                OutgoingButton(
                    f"choose:{card.source_key}",
                    f"{card.title[:18]} · {card.subtitle}",
                )
                for card in reply.cards if card.source_key
            )
            return response(reply, buttons)
        return response(reply)
