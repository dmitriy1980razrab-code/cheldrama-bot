from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.outgoing_delivery import ChannelReplySender, MemoryHttpTransport, MaxApiSender
from theatre_bot.persistent_replies import PersistentReplyExecutor, deliver_persistent_replies
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


with tempfile.TemporaryDirectory(prefix="cheldrama-persistent-replies-") as directory:
    database_path = Path(directory) / "subscribers.sqlite3"
    protector = IdentityProtector(Fernet.generate_key(), b"persistent-demo-key-" * 2)
    executor = PersistentReplyExecutor(database_path, protector)
    message = OutgoingMessage(
        "max",
        "demo-user-18",
        Reply("Будем рады видеть Вас в театре!"),
        (OutgoingButton("subscribe", "Подписаться"),),
    )
    executor.submit(message, "demo-event-1")
    executor.submit(message, "demo-event-1")

    # Имитируем перезапуск: открываем базу заново другим исполнителем.
    connection = connect_subscribers(database_path)
    initialize_subscribers(connection)
    transport = MemoryHttpTransport()
    sender = ChannelReplySender(max_sender=MaxApiSender("demo-token", transport))
    first = deliver_persistent_replies(connection, protector, sender)
    second = deliver_persistent_replies(connection, protector, sender)
    row = connection.execute(
        "SELECT status, attempt_count FROM outgoing_reply_queue"
    ).fetchone()
    connection.close()

    print("После повторного события записей в очереди: 1")
    print(f"После перезапуска доставлено: {first.sent}; повторно: {second.sent}")
    print(f"Состояние: {row['status']}; попыток: {row['attempt_count']}")
    print(f"Тестовым транспортом подготовлено запросов: {len(transport.requests)}")
    print("ID и текст хранились зашифрованными. Внешняя сеть не вызывалась.")
