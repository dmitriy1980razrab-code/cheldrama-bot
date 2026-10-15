from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import sqlite3

from theatre_bot.subscribers import (
    IdentityProtector,
    performance_subscription,
    unsubscribe_from_performance,
    available_subscription_actions,
    configure_play_notifications,
    record_consent,
    subscribe_to_play,
    unsubscribe,
)


class FlowState(str, Enum):
    OFFER = "offer"
    PERSONAL_DATA = "personal_data"
    SERVICE_NOTIFICATIONS = "service_notifications"
    MARKETING = "marketing"
    ACTIVE = "active"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class FlowButton:
    action: str
    label: str


@dataclass(frozen=True)
class FlowReply:
    text: str
    buttons: tuple[FlowButton, ...]


@dataclass(frozen=True)
class SubscriptionSession:
    channel: str
    external_id: str
    display_name: str | None
    play_key: str
    play_title: str
    state: FlowState = FlowState.OFFER
    performance_key: str | None = None
    selected_starts_at: str | None = None


LEGAL_DOCUMENTS = (
    ("Политика обработки персональных данных", "/legal/privacy"),
    ("Согласие на обработку персональных данных", "/legal/personal-data-consent"),
    ("Согласие на сервисные уведомления", "/legal/service-notifications"),
    ("Согласие на рекламные сообщения", "/legal/advertising"),
)

DOCUMENT_VERSIONS = {
    "personal_data": "draft-v1",
    "service_notifications": "draft-v1",
    "marketing": "draft-v1",
}



def _session_title(session: SubscriptionSession) -> str:
    if not session.selected_starts_at:
        return session.play_title
    moment = datetime.fromisoformat(session.selected_starts_at)
    return f"{session.play_title} ({moment:%d.%m.%Y %H:%M})"


def legal_documents_reply() -> FlowReply:
    lines = ["Правовые документы:"] + [
        f"• {title}: {path}" for title, path in LEGAL_DOCUMENTS
    ]
    return FlowReply("\n".join(lines), (FlowButton("back", "Назад"),))


def subscription_menu(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    session: SubscriptionSession,
) -> FlowReply:
    if session.performance_key is not None:
        active = performance_subscription(
            connection, protector, session.channel, session.external_id,
            session.performance_key,
        )
        actions = ("configure", "unsubscribe") if active is not None else ("subscribe",)
    else:
        actions = available_subscription_actions(
            connection, protector, session.channel, session.external_id
        )
    if "unsubscribe" in actions:
        return FlowReply(
            f"Вы подписаны на уведомления о спектакле «{_session_title(session)}».",
            (
                FlowButton("configure", "Настроить уведомления"),
                FlowButton("unsubscribe", "Отписаться"),
                FlowButton("legal", "Правовые документы"),
            ),
        )
    return FlowReply(
        f"Хотите получать сообщения о переносе или отмене спектакля «{_session_title(session)}» "
        "и напоминание за сутки?",
        (
            FlowButton("subscribe", "Подписаться"),
            FlowButton("legal", "Правовые документы"),
        ),
    )


def handle_subscription_action(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    session: SubscriptionSession,
    action: str,
) -> tuple[SubscriptionSession, FlowReply]:
    if action == "legal":
        return session, legal_documents_reply()

    if session.performance_key is not None:
        active = performance_subscription(
            connection, protector, session.channel, session.external_id,
            session.performance_key,
        )
        if active is not None:
            session = replace(session, state=FlowState.ACTIVE)
        if action in {"configure", "notify_both", "notify_changes", "reminder_24h"}:
            if active is None:
                return session, subscription_menu(connection, protector, session)
        if action == "subscribe" and active is not None:
            return session, subscription_menu(connection, protector, session)

    if action == "subscribe" and session.state in {FlowState.OFFER, FlowState.CANCELLED}:
        updated = replace(session, state=FlowState.PERSONAL_DATA)
        return updated, FlowReply(
            "Перед подпиской ознакомьтесь с политикой и подтвердите согласие на "
            "обработку персональных данных.",
            (
                FlowButton("legal", "Открыть документы"),
                FlowButton("accept_personal_data", "Согласен"),
                FlowButton("cancel", "Отмена"),
            ),
        )

    if action == "accept_personal_data" and session.state == FlowState.PERSONAL_DATA:
        record_consent(
            connection, protector, session.channel, session.external_id,
            "personal_data", DOCUMENT_VERSIONS["personal_data"], True,
            "channel_bot", display_name=session.display_name,
        )
        updated = replace(session, state=FlowState.SERVICE_NOTIFICATIONS)
        return updated, FlowReply(
            "Разрешаете отправлять сервисные уведомления о переносе, отмене и "
            "напоминание за сутки?",
            (
                FlowButton("accept_service", "Разрешаю"),
                FlowButton("cancel", "Отмена"),
            ),
        )

    if action == "accept_service" and session.state == FlowState.SERVICE_NOTIFICATIONS:
        record_consent(
            connection, protector, session.channel, session.external_id,
            "service_notifications", DOCUMENT_VERSIONS["service_notifications"], True,
            "channel_bot", display_name=session.display_name,
        )
        updated = replace(session, state=FlowState.MARKETING)
        return updated, FlowReply(
            "Хотите отдельно получать предложения театра с учётом Ваших интересов? "
            "Отказ не влияет на сервисные уведомления.",
            (
                FlowButton("accept_marketing", "Да, хочу"),
                FlowButton("decline_marketing", "Нет, спасибо"),
            ),
        )

    if action in {"accept_marketing", "decline_marketing"} and session.state == FlowState.MARKETING:
        granted = action == "accept_marketing"
        record_consent(
            connection, protector, session.channel, session.external_id,
            "marketing", DOCUMENT_VERSIONS["marketing"], granted,
            "channel_bot", display_name=session.display_name,
        )
        subscribe_to_play(
            connection, protector, session.channel, session.external_id,
            session.play_key, session.play_title,
            performance_key=session.performance_key,
            selected_starts_at=session.selected_starts_at,
        )
        updated = replace(session, state=FlowState.ACTIVE)
        marketing_text = (
            "Предложения театра также разрешены."
            if granted else "Рекламные сообщения отправляться не будут."
        )
        return updated, FlowReply(
            f"Готово! Вы подписаны на «{_session_title(session)}»: сообщим о переносе "
            f"или отмене и напомним за сутки. {marketing_text}",
            (
                FlowButton("configure", "Настроить"),
                FlowButton("unsubscribe", "Отписаться"),
            ),
        )

    if action == "unsubscribe":
        if session.performance_key is not None:
            changed = unsubscribe_from_performance(
                connection, protector, session.channel, session.external_id,
                session.performance_key,
            )
        else:
            changed = unsubscribe(
                connection, protector, session.channel, session.external_id,
                DOCUMENT_VERSIONS["service_notifications"], "channel_bot",
            )
        if not changed:
            return session, subscription_menu(connection, protector, session)
        updated = replace(session, state=FlowState.CANCELLED)
        return updated, FlowReply(
            ("\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 \u044d\u0442\u043e\u0442 \u043f\u043e\u043a\u0430\u0437 \u043e\u0442\u043a\u043b\u044e\u0447\u0435\u043d\u0430." if session.performance_key is not None else "Подписка отключена. Сервисные и рекламные сообщения больше не будут приходить."),
            (FlowButton("subscribe", "Подписаться снова"),),
        )

    if action == "configure" and session.state == FlowState.ACTIVE:
        return session, FlowReply(
            f"Какие уведомления о спектакле «{_session_title(session)}» Вы хотите получать?",
            (
                FlowButton("notify_both", "Все уведомления"),
                FlowButton("notify_changes", "Только перенос или отмена"),
                FlowButton("reminder_24h", "Только напоминание за сутки"),
                FlowButton("unsubscribe", "Отписаться"),
            ),
        )

    notification_options = {
        "notify_both": (True, True, "Все уведомления включены."),
        "notify_changes": (True, False, "Оставлены только сообщения о переносе или отмене."),
        "reminder_24h": (False, True, "Оставлено только напоминание за сутки."),
    }
    if action in notification_options and session.state == FlowState.ACTIVE:
        changes, reminder, message = notification_options[action]
        changed = configure_play_notifications(
            connection, protector, session.channel, session.external_id,
            (f"performance:{session.performance_key}"
             if session.performance_key is not None else session.play_key),
            changes, reminder,
        )
        if changed:
            return session, FlowReply(
                message,
                (
                    FlowButton("configure", "Изменить настройки"),
                    FlowButton("unsubscribe", "Отписаться"),
                ),
            )

    if action == "cancel":
        return replace(session, state=FlowState.CANCELLED), FlowReply(
            "Оформление подписки отменено.",
            (FlowButton("subscribe", "Вернуться к подписке"),),
        )

    return session, FlowReply(
        "Это действие сейчас недоступно. Вернитесь в меню подписки.",
        (FlowButton("menu", "Меню подписки"),),
    )
