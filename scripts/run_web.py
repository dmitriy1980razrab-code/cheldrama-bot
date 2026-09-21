from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.api import run_server


PROJECT_ROOT = Path(__file__).parents[1]

if __name__ == "__main__":
    run_server(
        database_path=PROJECT_ROOT / "data" / "theatre.sqlite3",
        web_root=PROJECT_ROOT / "web",
        host=os.environ.get("THEATRE_BOT_HOST", "127.0.0.1"),
        port=int(os.environ.get("THEATRE_BOT_PORT", "8080")),
    )
