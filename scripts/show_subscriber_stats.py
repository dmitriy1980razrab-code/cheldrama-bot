from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.subscribers import connect_subscribers, initialize_subscribers, subscriber_stats


database_path = Path(__file__).parents[1] / "data" / "subscribers.sqlite3"
connection = connect_subscribers(database_path)
initialize_subscribers(connection)
stats = subscriber_stats(connection)
connection.close()

print(f"База подписчиков: {database_path}")
print("Активные подписчики:")
for channel in ("vk", "max"):
    print(f"  {channel.upper()}: {stats.active_by_channel.get(channel, 0)}")
print(f"Согласий на маркетинговые сообщения: {stats.marketing_consents}")
print("Популярные подписки на спектакли:")
for title, count in stats.active_play_subscriptions:
    print(f"  {title}: {count}")
print("Предпочтения:")
for kind, label, count in stats.preferences:
    print(f"  {kind} — {label}: {count}")
