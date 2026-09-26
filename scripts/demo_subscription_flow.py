from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.subscription_flow import (
    SubscriptionSession,
    handle_subscription_action,
    subscription_menu,
)
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


def show(title, reply):
    print(f"\n{title}")
    print(reply.text)
    print("Кнопки: " + ", ".join(button.label for button in reply.buttons))


with tempfile.TemporaryDirectory(prefix="cheldrama-flow-demo-") as directory:
    connection = connect_subscribers(Path(directory) / "subscribers.sqlite3")
    initialize_subscribers(connection)
    protector = IdentityProtector(Fernet.generate_key(), b"flow-demo-key-" * 3)
    session = SubscriptionSession(
        "vk", "demo-vk-visitor", "Анна", "hamlet", "Гамлет"
    )

    show("1. Предложение подписки", subscription_menu(connection, protector, session))
    steps = (
        ("subscribe", "2. Обработка персональных данных"),
        ("accept_personal_data", "3. Сервисные уведомления"),
        ("accept_service", "4. Отдельное рекламное согласие"),
        ("decline_marketing", "5. Подписка без рекламы"),
        ("configure", "6. Настройка уведомлений"),
        ("notify_changes", "7. Изменённые настройки"),
        ("unsubscribe", "8. Отписка"),
    )
    for action, title in steps:
        session, reply = handle_subscription_action(
            connection, protector, session, action
        )
        show(title, reply)

    show("9. Меню после отписки", subscription_menu(connection, protector, session))
    connection.close()

print("\nВнешние сообщения не отправлялись. Временная база удалена.")
