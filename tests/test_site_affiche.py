from datetime import date
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.site_affiche import discover_affiche_month_urls, parse_affiche


HTML = """
<div class="performance nomobile"><div class="container"><div class="row">
  <div class="left"><div class="datetime">
    <div class="day garamond">20</div>
    <div class="month garamond">сентября, воскресенье</div>
    <div class="time arsenal brown">17:00</div>
  </div></div>
  <div class="play"><div class="more">
    <a href="/plays/pyat-vecherov/"><img src="/media/play.jpg"></a>
  </div></div>
  <div class="play"><h2><a href="/plays/pyat-vecherov/">Пять вечеров</a></h2>
    <div class="subgenre brown">История одной любви, 16+</div>
    <div class="line duration brown">1 час 50 минут без антракта</div>
    <div class="line scene brown"><a href="#">Малая сцена</a></div>
  </div>
  <div class="tickets"><a href="#" data-kassy-event="1067">Купить билет</a></div>
</div></div></div>
"""


class AfficheParserTests(unittest.TestCase):
    def test_parses_performance(self):
        items = parse_affiche(HTML, today=date(2026, 9, 18))
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "Пять вечеров")
        self.assertEqual(item.play_url, "https://www.cheldrama.ru/plays/pyat-vecherov/")
        self.assertEqual(item.starts_at, "2026-09-20T17:00")
        self.assertEqual(item.genre, "История одной любви")
        self.assertEqual(item.age_rating, "16+")
        self.assertEqual(item.venue, "Малая сцена")
        self.assertEqual(item.ticket_event_id, "1067")

    def test_discovers_month_pages(self):
        html = '<a href="/affiche/2026/09/">Сентябрь</a><a href="/affiche/2026/10/">Октябрь</a>'
        self.assertEqual(
            discover_affiche_month_urls(html),
            [
                "https://www.cheldrama.ru/affiche/2026/09/",
                "https://www.cheldrama.ru/affiche/2026/10/",
            ],
        )


if __name__ == "__main__":
    unittest.main()
