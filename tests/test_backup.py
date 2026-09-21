from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import hashlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.backup import create_database_backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "theatre.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('данные')")
        connection.commit()
        connection.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_backup_is_valid_and_has_matching_checksum(self):
        result = create_database_backup(self.database, self.root / "backups")
        digest = hashlib.sha256(result.archive_path.read_bytes()).hexdigest()
        self.assertTrue(result.checksum_path.read_text(encoding="ascii").startswith(digest))

        restored = self.root / "restored.sqlite3"
        with gzip.open(result.archive_path, "rb") as source:
            restored.write_bytes(source.read())
        connection = sqlite3.connect(restored)
        self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "данные")
        connection.close()

    def test_retention_keeps_requested_number(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for day in range(3):
            create_database_backup(
                self.database,
                self.root / "backups",
                keep=2,
                now=start + timedelta(days=day),
            )
        self.assertEqual(len(list((self.root / "backups").glob("theatre-*.sqlite3.gz"))), 2)
