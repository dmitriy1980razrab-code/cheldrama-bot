from datetime import datetime
from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channel_handler import ChannelMessageHandler
from theatre_bot.channels import IncomingMessage
from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


def show(result):
    print(result.reply.text)
    if result.buttons:
        print("Кнопки: " + ", ".join(button.label for button in result.buttons))
    print()


with tempfile.TemporaryDirectory(prefix="cheldrama-channel-events-") as directory:
    root = Path(directory)
    theatre = connect(root / "theatre.sqlite3")
    initialize(theatre)
    sync_affiche(
        theatre,
        [AfficheItem(
            title="Гамлет",
            play_url="https://www.cheldrama.ru/plays/hamlet/",
            starts_at="2026-10-10T19:00",
            genre="Драма", age_rating="16+", duration=None,
            venue="Большая сцена", image_url=None, ticket_event_id="demo-event",
        )],
        now=datetime(2026, 10, 1, 12, 0),
    )
    subscribers = connect_subscribers(root / "subscribers.sqlite3")
    initialize_subscribers(subscribers)
    protector = IdentityProtector(Fernet.generate_key(), b"event-demo-key-" * 3)
    handler = ChannelMessageHandler(theatre, subscribers, protector)
    now = datetime(2026, 10, 1, 12, 0)

    print("Тестовое событие VK: вопрос о спектакле")
    show(handler.process(IncomingMessage("vk", "demo-user", "1", "Когда Гамлет?", "Анна"), now))
    for event_id, action in enumerate((
        "subscribe", "accept_personal_data", "accept_service", "accept_marketing"
    ), start=2):
        print(f"Тестовое нажатие VK: {action}")
        show(handler.process(IncomingMessage("vk", "demo-user", str(event_id), "", "Анна", action), now))

    duplicate = handler.process(
        IncomingMessage("vk", "demo-user", "5", "", "Анна", "accept_marketing"), now
    )
    print("Повтор события обработан:", "да" if duplicate else "нет")
    theatre.close()
    subscribers.close()

print("Внешние API не вызывались. Временные базы удалены.")
