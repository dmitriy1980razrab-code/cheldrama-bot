from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from theatre_bot.site_affiche import BASE_URL, fetch_affiche_html


REPERTOIRE_URL = f"{BASE_URL}/plays/kind/repertoire/"
CHILDREN_URL = f"{BASE_URL}/plays/kind/childrens/"


@dataclass(frozen=True)
class RepertoireItem:
    title: str
    play_url: str
    catalog_kind: str


class _RepertoireParser(HTMLParser):
    def __init__(self, catalog_kind: str) -> None:
        super().__init__(convert_charrefs=True)
        self.catalog_kind = catalog_kind
        self.in_h3 = False
        self.href: str | None = None
        self.buffer: list[str] = []
        self.items: list[RepertoireItem] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "h3":
            self.in_h3 = True
        elif tag == "a" and self.in_h3:
            href = attributes.get("href") or ""
            if href.startswith("/plays/"):
                self.href = href
                self.buffer = []

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            title = " ".join(" ".join(self.buffer).split())
            if title:
                self.items.append(
                    RepertoireItem(title, urljoin(BASE_URL, self.href), self.catalog_kind)
                )
            self.href = None
            self.buffer = []
        elif tag == "h3":
            self.in_h3 = False

    def handle_data(self, data):
        if self.href:
            self.buffer.append(data)


def parse_repertoire(html_text: str, catalog_kind: str) -> list[RepertoireItem]:
    parser = _RepertoireParser(catalog_kind)
    parser.feed(html_text)
    unique: dict[str, RepertoireItem] = {}
    for item in parser.items:
        unique[item.play_url] = item
    return list(unique.values())


def fetch_full_repertoire() -> list[RepertoireItem]:
    repertoire = parse_repertoire(fetch_affiche_html(REPERTOIRE_URL), "repertoire")
    children = parse_repertoire(fetch_affiche_html(CHILDREN_URL), "children")
    combined: dict[str, RepertoireItem] = {item.play_url: item for item in repertoire}
    combined.update({item.play_url: item for item in children})
    return list(combined.values())

