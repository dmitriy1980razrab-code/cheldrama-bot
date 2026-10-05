from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Callable

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
    "deploy/systemd/cheldrama-bot.service",
    "deploy/systemd/cheldrama-sync.service",
    "deploy/systemd/cheldrama-sync.timer",
    "deploy/scripts/cheldrama-backup-to-cloud.sh",
    "deploy/object-storage-lifecycle.json",
    "deploy/nginx/cheldrama-bot.conf",
    "deploy/runtime.env.example",
    "docs/INTEGRATION_CHECKLIST.md",
)

SystemctlCheck = Callable[[str, str], bool]


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


def _systemctl_check(action: str, unit: str) -> bool:
    if not Path("/run/systemd/system").is_dir():
        return False
    try:
        result = subprocess.run(
            ("systemctl", action, "--quiet", unit),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _deployment_checks(
    systemd_directory: Path,
    systemctl_check: SystemctlCheck | None,
) -> list[CheckResult]:
    check = systemctl_check or _systemctl_check
    runtime_available = systemctl_check is not None or Path("/run/systemd/system").is_dir()
    installed = all(
        (systemd_directory / unit).is_file()
        for unit in (
            "cheldrama-bot.service",
            "cheldrama-sync.service",
            "cheldrama-sync.timer",
            "cheldrama-backup.service",
            "cheldrama-backup.timer",
        )
    )
    if not runtime_available or not installed:
        return [
            CheckResult("WAIT", "Внешний сервер", "проверяется на развёрнутом Linux-сервере"),
            CheckResult(
                "WAIT",
                "Внешняя резервная копия",
                "проверяется на развёрнутом Linux-сервере",
            ),
        ]

    bot_ready = check("is-enabled", "cheldrama-bot.service") and check(
        "is-active", "cheldrama-bot.service"
    )
    sync_ready = check("is-enabled", "cheldrama-sync.timer") and check(
        "is-active", "cheldrama-sync.timer"
    )
    backup_ready = check("is-enabled", "cheldrama-backup.timer") and check(
        "is-active", "cheldrama-backup.timer"
    )

    server_level = "OK" if bot_ready and sync_ready else "WARN"
    server_details = (
        "бот и автоматическое обновление активны"
        if server_level == "OK"
        else "проверьте cheldrama-bot.service и cheldrama-sync.timer"
    )
    backup_level = "OK" if backup_ready else "WARN"
    backup_details = (
        "ежедневный таймер активен; результат загрузки проверяется журналом"
        if backup_level == "OK"
        else "проверьте cheldrama-backup.timer и последнюю загрузку"
    )
    return [
        CheckResult(server_level, "Внешний сервер", server_details),
        CheckResult(backup_level, "Внешняя резервная копия", backup_details),
    ]


def _https_check(nginx_directory: Path) -> CheckResult:
    if not nginx_directory.is_dir():
        return CheckResult(
            "WAIT",
            "HTTPS и домен",
            "проверяется на развёрнутом сервере с Nginx",
        )

    configurations: list[str] = []
    try:
        candidates = tuple(nginx_directory.iterdir())
    except OSError:
        candidates = ()
    for path in candidates:
        try:
            configurations.append(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            continue

    content = "\n".join(configurations)
    has_tls_listener = bool(
        re.search(r"^\s*listen\s+(?:\[::\]:)?443(?:\s+ssl)?\s*;", content, re.MULTILINE)
    )
    has_certificate = "ssl_certificate " in content and "ssl_certificate_key " in content
    if not (has_tls_listener and has_certificate):
        return CheckResult(
            "WAIT",
            "HTTPS и домен",
            "нужны домен и TLS-сертификат",
        )

    names: set[str] = set()
    for value in re.findall(r"^\s*server_name\s+([^;]+);", content, re.MULTILINE):
        names.update(name for name in value.split() if name != "_")
    details = "TLS настроен в активной конфигурации Nginx"
    if names:
        details += "; домены: " + ", ".join(sorted(names))
    return CheckResult("OK", "HTTPS и домен", details)


def readiness_report(
    project_root: str | Path,
    database_path: str | Path,
    now: datetime | None = None,
    systemd_directory: str | Path = "/etc/systemd/system",
    nginx_directory: str | Path = "/etc/nginx/sites-enabled",
    systemctl_check: SystemctlCheck | None = None,
) -> list[CheckResult]:
    root = Path(project_root)
    results = _file_checks(root)
    results.extend(_database_checks(Path(database_path), now))
    results.extend(_deployment_checks(Path(systemd_directory), systemctl_check))
    results.append(_https_check(Path(nginx_directory)))
    results.extend(
        (
            CheckResult("WAIT", "Сайт театра", "нужны согласование и доступ администратора"),
            CheckResult("WAIT", "VK", "нужны официальные доступы сообщества"),
            CheckResult("WAIT", "MAX", "нужны официальные параметры подключения"),
        )
    )
    return results
