from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize


DATABASE_PATH = Path(__file__).parents[1] / "data" / "theatre.sqlite3"

if __name__ == "__main__":
    connection = connect(DATABASE_PATH)
    initialize(connection)
    try:
        rows = connection.execute(
            "SELECT intent, variant, channel, template FROM response_templates ORDER BY intent, variant"
        ).fetchall()
    finally:
        connection.close()
    for row in rows:
        print(f"[{row['intent']}.{row['variant']} | {row['channel']}] {row['template']}")

