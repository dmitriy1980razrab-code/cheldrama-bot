from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import (
    connect,
    initialize,
    play_sources,
    record_sync_success,
    save_play_details,
    sync_affiche,
    sync_repertoire,
)
from theatre_bot.site_affiche import fetch_full_affiche
from theatre_bot.site_play import fetch_play_html, parse_play
from theatre_bot.site_repertoire import fetch_full_repertoire
from theatre_bot.technical_log import create_technical_logger, record_error
from theatre_bot.update_lock import UpdateAlreadyRunning, update_lock


PROJECT_ROOT = Path(__file__).parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "theatre.sqlite3"
LOCK_PATH = DATA_DIR / "sync.lock"


def run_update() -> None:
    connection = connect(DATABASE_PATH)
    try:
        initialize(connection)

        print("[1/3] Обновление репертуара...")
        repertoire = fetch_full_repertoire()
        added, updated, deactivated = sync_repertoire(connection, repertoire)
        print(
            f"Репертуар: {len(repertoire)}; добавлено: {added}; "
            f"изменено: {updated}; снято: {deactivated}"
        )

        print("[2/3] Обновление афиши...")
        affiche = fetch_full_affiche(
            progress=lambda number, total, url: print(
                f"Загрузка месяца {number}/{total}"
            )
        )
        report = sync_affiche(connection, affiche)
        print(
            f"Афиша: {len(affiche)}; добавлено: {report.performances_added}; "
            f"обновлено: {report.performances_updated}"
        )

        print("[3/3] Обновление сведений о спектаклях...")
        sources = play_sources(connection, missing_details_only=False)
        for number, (play_id, url) in enumerate(sources, start=1):
            details = parse_play(fetch_play_html(url))
            save_play_details(connection, play_id, details)
            print(f"[{number}/{len(sources)}] {details.title}")
            if number < len(sources):
                time.sleep(0.5)
        record_sync_success(connection, "play_details", len(sources))
        print(f"Сведения обновлены: {len(sources)}")
    finally:
        connection.close()


if __name__ == "__main__":
    logger = create_technical_logger(DATA_DIR / "technical.log")
    try:
        with update_lock(LOCK_PATH):
            run_update()
    except UpdateAlreadyRunning:
        print("Обновление уже выполняется. Повторный запуск остановлен.")
        raise SystemExit(2)
    except Exception as error:
        record_error(logger, "sync.all", error)
        print(f"Обновление остановлено из-за ошибки ({type(error).__name__}).")
        print("Ранее успешно завершённые части базы сохранены.")
        raise SystemExit(1)
    print("Полное обновление базы успешно завершено.")
