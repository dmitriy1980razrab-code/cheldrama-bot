from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, play_sources, save_play_details
from theatre_bot.site_play import fetch_play_html, parse_play


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"


if __name__ == "__main__":
    connection = connect(DATABASE_PATH)
    initialize(connection)
    sources = play_sources(connection)
    try:
        for number, (play_id, url) in enumerate(sources, start=1):
            details = parse_play(fetch_play_html(url))
            save_play_details(connection, play_id, details)
            print(f"[{number}/{len(sources)}] {details.title}")
            if number < len(sources):
                time.sleep(0.5)
    finally:
        connection.close()

    print("Подробные сведения о спектаклях обновлены.")

