from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.persistent_delivery_worker import reply_queue_status


database_path = Path(__file__).parents[1] / "data" / "subscribers.sqlite3"
status = reply_queue_status(database_path)

print(f"База подписчиков: {database_path}")
print(f"Ожидают доставки: {status.pending}")
print(f"Обрабатываются: {status.processing}")
print(f"Ожидают повтора после ошибки: {status.failed}")
print(f"Доставлены: {status.sent}")
print(f"Отменены после повторной проверки: {status.cancelled}")
print("Персональные данные и тексты сообщений в отчёт не выводятся.")
