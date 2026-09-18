from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_repertoire
from theatre_bot.site_repertoire import fetch_full_repertoire


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"


if __name__ == "__main__":
    items = fetch_full_repertoire()
    connection = connect(DATABASE_PATH)
    try:
        initialize(connection)
        added, updated, deactivated = sync_repertoire(connection, items)
    finally:
        connection.close()
    print(f"Найдено в действующем репертуаре: {len(items)}")
    print(f"Добавлено: {added}")
    print(f"Изменено: {updated}")
    print(f"Снято с репертуара: {deactivated}")

