from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.backup import create_database_backup
from theatre_bot.technical_log import create_technical_logger, record_error
from theatre_bot.update_lock import UpdateAlreadyRunning, update_lock


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"
BACKUP_DIR = Path(os.environ.get("THEATRE_BACKUP_DIR", PROJECT_ROOT / "backups"))
KEEP = int(os.environ.get("THEATRE_BACKUP_KEEP", "14"))


if __name__ == "__main__":
    logger = create_technical_logger(BACKUP_DIR / "technical.log")
    try:
        with update_lock(BACKUP_DIR / "backup.lock"):
            result = create_database_backup(DATABASE_PATH, BACKUP_DIR, keep=KEEP)
    except UpdateAlreadyRunning:
        print("Резервное копирование уже выполняется.")
        raise SystemExit(2)
    except Exception as error:
        record_error(logger, "backup.database", error)
        print(f"Резервное копирование остановлено ({type(error).__name__}).")
        raise SystemExit(1)

    print(f"Копия создана: {result.archive_path.name}")
    print(f"Контрольная сумма: {result.checksum_path.name}")
    print(f"Старых копий удалено: {result.removed_count}")
