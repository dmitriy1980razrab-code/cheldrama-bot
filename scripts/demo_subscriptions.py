from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.demo_channel import run_demo_scenario


report = run_demo_scenario()

print("Демонстрационный контур VK/MAX")
print(f"Вымышленных подписчиков создано: {report.subscribers_created}")
print("Получатели рекламной кампании: " + ", ".join(report.campaign_recipients))
print("Тестовые отправки:")
for delivery in report.deliveries:
    print(f"  {delivery.channel.upper()} | {delivery.kind} | {delivery.external_id}")
print(f"Отписка проверена: {report.unsubscribed_name}")
print("Внешние сообщения не отправлялись.")
print("Временная демонстрационная база удалена.")
