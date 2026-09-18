from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.site_play import parse_play


HTML = """
<div class="play-header"><h1 class="arsenal">Пять вечеров</h1></div>
<div class="container play-info"><div class="info"><div class="more">
  <div class="any"><dl>
    <dt><a href="/theatre/people/person/director/">Денис Хуснияров</a></dt>
    <dd>Режиссер-постановщик</dd>
  </dl></div>
  <div class="detail"><div class="point">Драма</div><div class="point">16+</div><div class="point">1 час</div></div>
</div></div>
<div class="info"><div class="more"><div class="text">
  <p style="text-align: right;">Цитата</p>
  <p>Небольшая аннотация спектакля.</p>
</div></div></div></div>
<div class="play-staff container"><h2>Действующие лица и исполнители</h2>
  <div class="text">
    <p>Ильин - <a href="/theatre/people/person/artist-one/">Первый Артист</a></p>
    <p>Катя - <a href="/theatre/people/person/artist-two/">Вторая Артистка</a>, <a href="/theatre/people/person/artist-three/">Третья Артистка</a></p>
  </div>
</div>
"""


class PlayParserTests(unittest.TestCase):
    def test_parses_details_and_cast(self):
        play = parse_play(HTML)
        self.assertEqual(play.title, "Пять вечеров")
        self.assertEqual(play.director, "Денис Хуснияров")
        self.assertEqual(play.summary, "Небольшая аннотация спектакля.")
        self.assertEqual(len(play.cast), 3)
        self.assertEqual(play.genre, "Драма")
        self.assertEqual(play.age_rating, "16+")
        self.assertEqual(play.cast[0].role_name, "Ильин")
        self.assertEqual(play.cast[1].artist_name, "Вторая Артистка")


if __name__ == "__main__":
    unittest.main()
