from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

from theatre_bot.database import data_status


@dataclass(frozen=True)
class CheckResult:
    level: str
    name: str
    details: str


REQUIRED_FILES = (
    "Dockerfile",
    "compose.yaml",
    "scripts/run_web.py",
    "scripts/sync_all.py",
    "deploy/systemd/cheldrama-update.service",
    "deploy/systemd/cheldrama-update.timer",
    "scripts/backup_database.py",
    "deploy/systemd/cheldrama-backup.service",
    "deploy/systemd/cheldrama-backup.timer",
    "docs/INTEGRATION_CHECKLIST.md",
)


def _file_checks(project_root: Path) -> list[CheckResult]:
    missing = [name for name in REQUIRED_FILES if not (project_root / name).is_file()]
    if missing:
        return [CheckResult("ERROR", "Серверные файлы", "не найдены: " + ", ".join(missing))]

    compose = (project_root / "compose.yaml").read_text(encoding="utf-8")
    if '"127.0.0.1:8080:8080"' not in compose:
        return [CheckResult("ERROR", "Серверные файлы", "API не ограничен локальным портом")]
    return [CheckResult("OK", "Серверные файлы", "комплект готов")]


def _database_checks(database_path: Path, now: datetime | None) -> list[CheckResult]:
    if not database_path.is_file():
        return [CheckResult("ERROR", "База данных", "файл базы не найден")]

    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            return [CheckResult("ERROR", "База данных", "проверка целостности не пройдена")]

        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM plays WHERE is_active = 1) AS plays,
                (SELECT count(*) FROM artists WHERE is_active = 1) AS artists,
                (SELECT count(*) FROM performances WHERE status = 'scheduled') AS events
            """
        ).fetchone()
        results = [
            CheckResult(
                "OK",
                "База данных",
                f"спектаклей: {counts['plays']}; артистов: {counts['artists']}; событий: {counts['events']}",
            )
        ]
        for status in data_status(connection, now):
            level = "WARN" if status.is_stale else "OK"
            details = "требуется обновление" if status.is_stale else f"актуально; записей: {status.item_count}"
            results.append(CheckResult(level, f"Данные: {status.component}", details))
        return results
    except (sqlite3.Error, KeyError) as error:
        return [CheckResult("ERROR", "База данных", f"ошибка структуры: {type(error).__name__}")]
    finally:
        if "connection" in locals():
            connection.close()


def readiness_report(
    project_root: str | Path,
    database_path: str | Path,
    now: datetime | None = None,
) -> list[CheckResult]:
    root = Path(project_root)
    results = _file_checks(root)
    results.extend(_database_checks(Path(database_path), now))
    results.extend(
        (
            CheckResult("WAIT", "Внешний сервер", "не выбран и не оплачивается"),
            CheckResult("WAIT", "HTTPS и домен", "подключаются при развёртывании"),
            CheckResult("WAIT", "Сайт театра", "нужны согласование и доступ администратора"),
            CheckResult("WAIT", "VK", "нужны официальные доступы сообщества"),
            CheckResult("WAIT", "MAX", "нужны официальные параметры подключения"),
            CheckResult("WAIT", "Внешняя резервная копия", "подключается после выбора сервера"),
        )
    )
    return results
