from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.database import connect, initialize
from theatre_bot.dialog import answer


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"


def print_reply(reply) -> None:
    print(f"\nБот: {reply.text}")
    for number, card in enumerate(reply.cards, start=1):
        print(f"\n{number}. {card.title}")
        print(f"   {card.subtitle}")
        if card.details:
            print(f"   {card.details}")
        print(f"   Подробнее: {card.play_url}")


if __name__ == "__main__":
    connection = connect(DATABASE_PATH)
    initialize(connection)
    print("Локальный бот запущен. Для выхода введите: выход")
    history: list[str] = []
    try:
        while True:
            text = input("\nВы: ").strip()
            if text.casefold() in {"выход", "exit", "quit"}:
                break
            print_reply(answer(connection, text, history=tuple(history[-4:])))
            history.append(text)
    finally:
        connection.close()
