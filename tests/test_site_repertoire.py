from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.site_repertoire import parse_repertoire


HTML = """
<div><h3><a href="/plays/one/">Первый спектакль</a></h3></div>
<div><h3><a href="/plays/two/">Второй спектакль</a></h3></div>
<footer><a href="/plays/archive/">Архив</a></footer>
"""


class RepertoireParserTests(unittest.TestCase):
    def test_parses_only_play_headings(self):
        items = parse_repertoire(HTML, "repertoire")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "Первый спектакль")
        self.assertEqual(items[0].catalog_kind, "repertoire")


if __name__ == "__main__":
    unittest.main()

