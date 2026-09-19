from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, data_status, initialize


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"
NAMES = {
    "affiche": "Афиша",
    "repertoire": "Репертуар",
    "play_details": "Сведения о спектаклях",
}


if __name__ == "__main__":
    connection = connect(DATABASE_PATH)
    try:
        initialize(connection)
        statuses = data_status(connection)
    finally:
        connection.close()

    print(f"База: {DATABASE_PATH}")
    for status in statuses:
        moment = (
            status.completed_at.astimezone().strftime("%d.%m.%Y %H:%M")
            if status.completed_at
            else "успешное обновление ещё не зафиксировано"
        )
        state = "ТРЕБУЕТ ОБНОВЛЕНИЯ" if status.is_stale else "актуально"
        print(f"{NAMES[status.component]}: {state}; {moment}; записей: {status.item_count}")
