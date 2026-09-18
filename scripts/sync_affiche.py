from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.site_affiche import fetch_full_affiche


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"


if __name__ == "__main__":
    items = fetch_full_affiche(
        progress=lambda number, total, url: print(f"Загрузка месяца {number}/{total}: {url}")
    )
    connection = connect(DATABASE_PATH)
    try:
        initialize(connection)
        report = sync_affiche(connection, items)
    finally:
        connection.close()

    print(f"База: {DATABASE_PATH}")
    print(f"Событий получено с сайта: {len(items)}")
    print(f"Спектаклей добавлено: {report.plays_added}")
    print(f"Событий добавлено: {report.performances_added}")
    print(f"Событий обновлено: {report.performances_updated}")
    print(f"Без изменений: {report.performances_unchanged}")
