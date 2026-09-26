from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.admin_demo import (
    AdminDemoSecurity,
    render_dashboard,
    seed_admin_demo,
)
from theatre_bot.subscribers import IdentityProtector, connect_subscribers, initialize_subscribers


class AdminDemoTests(unittest.TestCase):
    def test_login_accepts_only_exact_password(self):
        security = AdminDemoSecurity("secret-password")
        self.assertIsNone(security.login("wrong"))
        self.assertIsNotNone(security.login("secret-password"))

    def test_session_expires(self):
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        security = AdminDemoSecurity("secret-password")
        session_id, _ = security.login("secret-password", now)
        self.assertIsNotNone(security.session(session_id, now + timedelta(minutes=29)))
        self.assertIsNone(security.session(session_id, now + timedelta(minutes=31)))

    def test_csrf_token_is_required(self):
        security = AdminDemoSecurity("secret-password")
        session_id, csrf = security.login("secret-password")
        self.assertFalse(security.valid_csrf(session_id, "wrong"))
        self.assertTrue(security.valid_csrf(session_id, csrf))

    def test_dashboard_escapes_subscriber_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "subscribers.sqlite3"
            protector = IdentityProtector(Fernet.generate_key(), b"d" * 32)
            seed_admin_demo(path, protector)
            connection = connect_subscribers(path)
            initialize_subscribers(connection)
            row = connection.execute("SELECT id FROM subscribers ORDER BY id LIMIT 1").fetchone()
            connection.execute(
                "UPDATE subscribers SET display_name_encrypted = ? WHERE id = ?",
                (protector.encrypt("<script>alert(1)</script>"), row["id"]),
            )
            connection.commit()
            page = render_dashboard(connection, protector, "csrf-token").decode("utf-8")
            connection.close()
            self.assertNotIn("<script>alert(1)</script>", page)
            self.assertIn("&lt;script&gt;", page)


if __name__ == "__main__":
    unittest.main()
