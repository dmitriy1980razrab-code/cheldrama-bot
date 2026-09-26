from pathlib import Path
import secrets
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.admin_demo import run_admin_demo


with tempfile.TemporaryDirectory(prefix="cheldrama-admin-demo-") as directory:
    run_admin_demo(
        Path(directory) / "subscribers.sqlite3",
        secrets.token_urlsafe(10),
    )

print("Временная демонстрационная база удалена.")
