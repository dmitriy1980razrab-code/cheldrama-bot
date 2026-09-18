from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.site_affiche import fetch_affiche_html, parse_affiche, preview_json


if __name__ == "__main__":
    html = fetch_affiche_html()
    items = parse_affiche(html)
    print(preview_json(items))
    print(f"\nНайдено событий: {len(items)}", file=sys.stderr)

