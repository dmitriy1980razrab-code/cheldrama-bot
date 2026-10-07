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
    def test_director_credit_with_yo(self):
        suffix = chr(1077) + chr(1088) + "-"
        replacement = chr(1105) + chr(1088) + "-"
        self.assertEqual(HTML.count(suffix), 1)
        html = HTML.replace(suffix, replacement, 1)
        expected = parse_play(HTML).director
        self.assertIsNotNone(expected)
        self.assertEqual(parse_play(html).director, expected)


    def test_director_credit_variants(self):
        director = "\u0420\u0435\u0436\u0438\u0441\u0441\u0451\u0440"
        staging = "\u041f\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0430"
        original = "\u0420\u0435\u0436\u0438\u0441\u0441\u0435\u0440-\u043f\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u0449\u0438\u043a"
        labels = (
            original + ", \u0445\u0443\u0434\u043e\u0436\u043d\u0438\u043a-\u043f\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u0449\u0438\u043a",
            director,
            staging,
            staging + " \u0438 \u043c\u0443\u0437\u044b\u043a\u0430\u043b\u044c\u043d\u043e\u0435 \u043e\u0444\u043e\u0440\u043c\u043b\u0435\u043d\u0438\u0435",
            director + " \u0438 \u0445\u0443\u0434\u043e\u0436\u043d\u0438\u043a-\u043f\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u0449\u0438\u043a",
        )
        expected = parse_play(HTML).director
        self.assertIsNotNone(expected)
        self.assertEqual(HTML.count(original), 1)
        for label in labels:
            with self.subTest(label=label):
                self.assertEqual(parse_play(HTML.replace(original, label, 1)).director, expected)

    def test_other_director_credits_do_not_override_play_director(self):
        director = "\u0420\u0435\u0436\u0438\u0441\u0441\u0451\u0440"
        chief = "\u0413\u043b\u0430\u0432\u043d\u044b\u0439 " + director.lower()
        plastic = director + " \u043f\u043e \u043f\u043b\u0430\u0441\u0442\u0438\u043a\u0435"
        for label in (chief, plastic):
            with self.subTest(label=label):
                html = "<h1>Test</h1><dl><dt>Other person</dt><dd>" + label + "</dd></dl>"
                self.assertIsNone(parse_play(html).director)
        html = (
            "<h1>Test</h1><dl><dt>Chief</dt><dd>" + chief +
            "</dd><dt>Play director</dt><dd>" + director +
            "</dd><dt>Plastic director</dt><dd>" + plastic + "</dd></dl>"
        )
        self.assertEqual(parse_play(html).director, "Play director")

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
