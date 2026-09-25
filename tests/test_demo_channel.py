from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.demo_channel import DemoOutbox, run_demo_scenario


class DemoChannelTests(unittest.TestCase):
    def test_full_scenario_uses_only_marketing_eligible_recipient(self):
        report = run_demo_scenario(
            datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(report.subscribers_created, 3)
        self.assertEqual(report.campaign_recipients, ("Анна",))
        marketing = [item for item in report.deliveries if item.kind == "marketing_demo"]
        self.assertEqual(len(marketing), 1)
        self.assertEqual(marketing[0].external_id, "demo-vk-anna")

    def test_demo_database_is_removed(self):
        report = run_demo_scenario()
        self.assertTrue(report.database_was_temporary)

    def test_demo_outbox_rejects_unknown_channel(self):
        outbox = DemoOutbox()
        with self.assertRaises(ValueError):
            outbox.send("email", "1", "text", "test")


if __name__ == "__main__":
    unittest.main()
