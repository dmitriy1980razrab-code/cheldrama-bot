from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.site_affiche import fetch_affiche_html, parse_affiche


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"


if __name__ == "__main__":
    items = parse_affiche(fetch_affiche_html())
    connection = connect(DATABASE_PATH)
    try:
        initialize(connection)
        report = sync_affiche(connection, items)
    finally:
        connection.close()

    print(f"База: {DATABASE_PATH}")
    print(f"Спектаклей добавлено: {report.plays_added}")
    print(f"Событий добавлено: {report.performances_added}")
    print(f"Событий обновлено: {report.performances_updated}")
    print(f"Без изменений: {report.performances_unchanged}")

