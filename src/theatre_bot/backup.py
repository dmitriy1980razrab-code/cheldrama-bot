from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import gzip
import hashlib
import os
import shutil
import sqlite3


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    checksum_path: Path
    removed_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prune(destination: Path, keep: int, prefix: str) -> int:
    archives = sorted(destination.glob(f"{prefix}-*.sqlite3.gz"), reverse=True)
    removed = 0
    for archive in archives[max(1, keep):]:
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        archive.unlink()
        checksum.unlink(missing_ok=True)
        removed += 1
    return removed


def create_database_backup(
    database_path: str | Path,
    destination: str | Path,
    keep: int = 14,
    now: datetime | None = None,
    prefix: str = "theatre",
) -> BackupResult:
    source_path = Path(database_path)
    if not source_path.is_file():
        raise FileNotFoundError("database file not found")

    backup_dir = Path(destination)
    backup_dir.mkdir(parents=True, exist_ok=True)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    if not prefix or not prefix.replace("-", "").isalnum():
        raise ValueError("invalid backup prefix")
    archive_path = backup_dir / f"{prefix}-{stamp}.sqlite3.gz"
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    temp_database = backup_dir / f".{stamp}.sqlite3.tmp"
    temp_archive = backup_dir / f".{stamp}.sqlite3.gz.tmp"
    temp_checksum = backup_dir / f".{stamp}.sha256.tmp"

    try:
        source = None
        target = None
        try:
            source = sqlite3.connect(source_path)
            target = sqlite3.connect(temp_database)
            source.backup(target)
            integrity = target.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError("backup integrity check failed")
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

        with temp_database.open("rb") as raw, gzip.open(temp_archive, "wb") as compressed:
            shutil.copyfileobj(raw, compressed)
        os.replace(temp_archive, archive_path)

        checksum = _sha256(archive_path)
        temp_checksum.write_text(
            f"{checksum}  {archive_path.name}\n",
            encoding="ascii",
        )
        os.replace(temp_checksum, checksum_path)
        removed = _prune(backup_dir, keep, prefix)
        return BackupResult(archive_path, checksum_path, removed)
    finally:
        temp_database.unlink(missing_ok=True)
        temp_archive.unlink(missing_ok=True)
        temp_checksum.unlink(missing_ok=True)
