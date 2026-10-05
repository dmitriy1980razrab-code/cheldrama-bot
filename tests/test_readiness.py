from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize, record_sync_success
from theatre_bot.readiness import REQUIRED_FILES, readiness_report


class ReadinessTests(unittest.TestCase):
    def test_missing_project_and_database_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = readiness_report(root, root / "missing.sqlite3")
            self.assertGreaterEqual(sum(item.level == "ERROR" for item in results), 2)

    def test_current_database_has_no_data_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in REQUIRED_FILES:
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

    def test_deployed_server_and_backup_are_reported_as_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            systemd_directory = Path(directory)
            for unit in (
                "cheldrama-bot.service",
                "cheldrama-sync.service",
                "cheldrama-sync.timer",
                "cheldrama-backup.service",
                "cheldrama-backup.timer",
            ):
                (systemd_directory / unit).write_text("test", encoding="utf-8")

            results = readiness_report(
                Path(__file__).parents[1],
                Path(__file__).parents[1] / "data" / "missing.sqlite3",
                systemd_directory=systemd_directory,
                systemctl_check=lambda action, unit: True,
            )
            by_name = {item.name: item for item in results}
            self.assertEqual(by_name["Внешний сервер"].level, "OK")
            self.assertEqual(by_name["Внешняя резервная копия"].level, "OK")

    def test_active_nginx_tls_configuration_is_reported_as_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            nginx_directory = Path(directory) / "sites-enabled"
            nginx_directory.mkdir()
            (nginx_directory / "cheldrama-bot").write_text(
                """
                server {
                    listen 443 ssl;
                    server_name vash-kapeldiner.ru www.vash-kapeldiner.ru;
                    ssl_certificate /etc/letsencrypt/live/vash-kapeldiner.ru/fullchain.pem;
                    ssl_certificate_key /etc/letsencrypt/live/vash-kapeldiner.ru/privkey.pem;
                }
                """,
                encoding="utf-8",
            )

            results = readiness_report(
                Path(__file__).parents[1],
                Path(__file__).parents[1] / "data" / "missing.sqlite3",
                nginx_directory=nginx_directory,
            )
            result = next(item for item in results if item.name == "HTTPS и домен")
            self.assertEqual(result.level, "OK")
            self.assertIn("vash-kapeldiner.ru", result.details)

    def test_nginx_without_tls_is_still_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            nginx_directory = Path(directory)
            (nginx_directory / "cheldrama-bot").write_text(
                "server { listen 80; server_name vash-kapeldiner.ru; }",
                encoding="utf-8",
            )
            results = readiness_report(
                Path(__file__).parents[1],
                Path(__file__).parents[1] / "data" / "missing.sqlite3",
                nginx_directory=nginx_directory,
            )
            result = next(item for item in results if item.name == "HTTPS и домен")
            self.assertEqual(result.level, "WAIT")
