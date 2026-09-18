from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"


if __name__ == "__main__":
    connection = connect(DATABASE_PATH)
    initialize(connection)
    try:
        rows = connection.execute(
            """
            SELECT created_at, channel, text
            FROM unrecognized_requests
            ORDER BY id DESC LIMIT 50
            """
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        print("Непонятых запросов пока нет.")
    for row in rows:
        print(f"{row['created_at']} | {row['channel']} | {row['text']}")

