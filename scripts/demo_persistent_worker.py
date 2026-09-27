from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.outgoing_delivery import ChannelReplySender, MemoryHttpTransport, MaxApiSender
from theatre_bot.persistent_delivery_worker import PersistentDeliveryWorker, reply_queue_status
from theatre_bot.persistent_replies import PersistentReplyExecutor
from theatre_bot.subscribers import IdentityProtector


with tempfile.TemporaryDirectory(prefix="cheldrama-persistent-worker-") as directory:
    database_path = Path(directory) / "subscribers.sqlite3"
    protector = IdentityProtector(Fernet.generate_key(), b"worker-demo-hash-key-" * 2)
    transport = MemoryHttpTransport()
    sender = ChannelReplySender(max_sender=MaxApiSender("demo-token", transport))

    # Ответ записывается до запуска исполнителя — имитация перезапуска сервиса.
    executor = PersistentReplyExecutor(database_path, protector)
    executor.submit(
        OutgoingMessage("max", "demo-user", Reply("Ответ после перезапуска")),
        "demo-worker-event",
    )

    worker = PersistentDeliveryWorker(
        database_path, protector, sender, poll_interval=0.1
    )
    worker.wait_until_idle()
    before_stop = worker.status()
    stopped = worker.close()
    queue = reply_queue_status(database_path)

    print(f"Циклов выполнено: {before_stop.cycles}; доставлено: {before_stop.sent}")
    print(f"Очередь: ожидает {queue.pending}; отправлено {queue.sent}")
    print(f"Безопасная остановка: {'да' if stopped else 'нет'}")
    print(f"Тестовым транспортом подготовлено запросов: {len(transport.requests)}")
    print("Внешняя сеть не вызывалась, содержимое очереди не расшифровывалось в отчёте.")
