from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.readiness import readiness_report


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "theatre.sqlite3"


if __name__ == "__main__":
    results = readiness_report(PROJECT_ROOT, DATABASE_PATH)
    for result in results:
        print(f"[{result.level}] {result.name}: {result.details}")

    errors = sum(result.level == "ERROR" for result in results)
    warnings = sum(result.level == "WARN" for result in results)
    waiting = sum(result.level == "WAIT" for result in results)
    print()
    if errors:
        print(f"Итог: требуется исправление; ошибок: {errors}.")
        raise SystemExit(1)
    print(
        f"Итог: ядро готово; предупреждений: {warnings}; "
        f"внешних подключений ожидается: {waiting}."
    )
