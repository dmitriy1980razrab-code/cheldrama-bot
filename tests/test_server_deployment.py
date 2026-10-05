import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).parents[1]


class ServerDeploymentTests(unittest.TestCase):
    def test_compose_keeps_service_private_and_data_persistent(self):
        content = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:8080:8080"', content)
        self.assertIn("theatre_data:/app/data", content)
        self.assertIn("no-new-privileges:true", content)
        self.assertIn("restart: unless-stopped", content)
        self.assertIn("theatre_data:/app/data:ro", content)
        self.assertIn("theatre_backups:/app/backups", content)

    def test_server_timer_runs_unified_updater(self):
        service = (
            PROJECT_ROOT / "deploy" / "systemd" / "cheldrama-update.service"
        ).read_text(encoding="utf-8")
        timer = (
            PROJECT_ROOT / "deploy" / "systemd" / "cheldrama-update.timer"
        ).read_text(encoding="utf-8")
        self.assertIn("--profile jobs run --rm updater", service)
        self.assertIn("Persistent=true", timer)

    def test_native_server_services_are_versioned(self):
        systemd = PROJECT_ROOT / "deploy" / "systemd"
        bot_service = (systemd / "cheldrama-bot.service").read_text(encoding="utf-8")
        sync_timer = (systemd / "cheldrama-sync.timer").read_text(encoding="utf-8")
        backup_service = (systemd / "cheldrama-backup.service").read_text(encoding="utf-8")
        backup_timer = (systemd / "cheldrama-backup.timer").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1", bot_service)
        self.assertIn("EnvironmentFile=-/etc/cheldrama-bot/runtime.env", bot_service)
        self.assertIn("scripts/run_web.py", bot_service)
        self.assertIn("00,06,12,18:15:00 UTC", sync_timer)
        self.assertIn("/usr/local/bin/cheldrama-backup-to-cloud", backup_service)
        self.assertIn("20:30:00 UTC", backup_timer)

    def test_nginx_template_uses_public_domain_and_private_application_port(self):
        content = (PROJECT_ROOT / "deploy" / "nginx" / "cheldrama-bot.conf").read_text(
            encoding="utf-8"
        )
        self.assertIn("server_name vash-kapeldiner.ru www.vash-kapeldiner.ru;", content)
        self.assertIn("proxy_pass http://127.0.0.1:8080;", content)

    def test_runtime_environment_template_contains_only_secret_paths(self):
        content = (PROJECT_ROOT / "deploy" / "runtime.env.example").read_text(
            encoding="utf-8"
        )
        self.assertIn("THEATRE_VK_ACCESS_TOKEN_FILE=/etc/cheldrama-bot/secrets/", content)
        self.assertIn("THEATRE_VK_GROUP_ID=", content)
        self.assertNotIn("THEATRE_VK_ACCESS_TOKEN=", content)

    def test_object_storage_backup_has_retention_policy(self):
        script = (
            PROJECT_ROOT / "deploy" / "scripts" / "cheldrama-backup-to-cloud.sh"
        ).read_text(encoding="utf-8")
        lifecycle = json.loads(
            (PROJECT_ROOT / "deploy" / "object-storage-lifecycle.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("storage s3 cp", script)
        self.assertIn("subscribers-*.sqlite3.gz", script)
        self.assertIn('for BACKUP_NAME in "${BACKUP_NAMES[@]}"', script)
        self.assertEqual(lifecycle["lifecycleRules"][0]["expiration"]["days"], "90")
