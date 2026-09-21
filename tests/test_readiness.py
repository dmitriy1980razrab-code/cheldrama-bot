from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, record_sync_success
from theatre_bot.readiness import readiness_report


class ReadinessTests(unittest.TestCase):
    def test_missing_project_and_database_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = readiness_report(root, root / "missing.sqlite3")
            self.assertGreaterEqual(sum(item.level == "ERROR" for item in results), 2)

    def test_current_database_has_no_data_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "Dockerfile",
                "compose.yaml",
                "scripts/run_web.py",
                "scripts/sync_all.py",
                "deploy/systemd/cheldrama-update.service",
                "deploy/systemd/cheldrama-update.timer",
                "docs/INTEGRATION_CHECKLIST.md",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                content = '"127.0.0.1:8080:8080"' if relative == "compose.yaml" else "test"
                path.write_text(content, encoding="utf-8")

            database_path = root / "data" / "theatre.sqlite3"
            connection = connect(database_path)
            initialize(connection)
            now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
            for component in ("affiche", "repertoire", "play_details"):
                record_sync_success(connection, component, 1, completed_at=now)
            connection.close()

            results = readiness_report(root, database_path, now)
            self.assertFalse(any(item.level in ("ERROR", "WARN") for item in results))
            self.assertTrue(any(item.level == "WAIT" for item in results))
