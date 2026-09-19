from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.api import run_server


PROJECT_ROOT = Path(__file__).parents[1]

if __name__ == "__main__":
    run_server(
        database_path=PROJECT_ROOT / "data" / "theatre.sqlite3",
        web_root=PROJECT_ROOT / "web",
    )

