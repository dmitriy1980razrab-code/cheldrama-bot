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

    def test_server_timer_runs_unified_updater(self):
        service = (
            PROJECT_ROOT / "deploy" / "systemd" / "cheldrama-update.service"
        ).read_text(encoding="utf-8")
        timer = (
            PROJECT_ROOT / "deploy" / "systemd" / "cheldrama-update.timer"
        ).read_text(encoding="utf-8")
        self.assertIn("--profile jobs run --rm updater", service)
        self.assertIn("Persistent=true", timer)
