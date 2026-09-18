from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen


BASE_URL = "https://www.cheldrama.ru"
AFFICHE_URL = f"{BASE_URL}/affiche/"

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


@dataclass(frozen=True)
class AfficheItem:
    title: str
    play_url: str
    starts_at: str
    genre: str | None
    age_rating: str | None
    duration: str | None
    venue: str | None
    image_url: str | None
    ticket_event_id: str | None


def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
    value = dict(attrs).get("class") or ""
    return set(value.split())


class _AfficheParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.performance_depth = 0
        self.capture: str | None = None
        self.capture_depth = 0
        self.in_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = _classes(attrs)

        if tag == "div" and {"performance", "nomobile"}.issubset(classes):
            self.current = {}
            self.performance_depth = 1
            return

        if self.current is None:
            return

        if tag == "h2":
            self.in_heading = True

        if tag == "div":
            self.performance_depth += 1

        field = None
        if "day" in classes:
            field = "day"
        elif "month" in classes:
            field = "month"
        elif "time" in classes:
            field = "time"
        elif "subgenre" in classes:
            field = "subgenre"
        elif "duration" in classes:
            field = "duration"
        elif "scene" in classes:
            field = "venue"

        if field:
            self.capture = field
            self.capture_depth = self.performance_depth

        if tag == "a":
            href = attributes.get("href") or ""
            if href.startswith("/plays/") and "play_url" not in self.current:
                self.current["play_url"] = href
            if href.startswith("/plays/") and self.in_heading:
                self.current["play_url"] = href
                self.capture = "title"
                self.capture_depth = self.performance_depth
            if "data-kassy-event" in attributes:
                self.current["ticket_event_id"] = attributes["data-kassy-event"] or ""

        if tag == "img" and "image_url" not in self.current:
            src = attributes.get("src")
            if src:
                self.current["image_url"] = src

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return

        if tag == "h2":
            self.in_heading = False

        if self.capture and self.capture_depth == self.performance_depth:
            self.capture = None

        if tag == "div":
            self.performance_depth -= 1
            if self.performance_depth == 0:
                if self.current.get("title") and self.current.get("play_url"):
                    self.items.append(self.current)
                self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is None or self.capture is None:
            return
        text = " ".join(data.split())
        if text:
            previous = self.current.get(self.capture, "")
            self.current[self.capture] = f"{previous} {text}".strip()


def fetch_affiche_html(url: str = AFFICHE_URL, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={"User-Agent": "cheldrama-bot/0.1 (+official-theatre-project)"},
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _split_genre_and_age(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    age = re.search(r"(?<!\d)(\d{1,2}\+)", value)
    genre = re.sub(r",?\s*\d{1,2}\+\s*$", "", value).strip(" ,") or None
    return genre, age.group(1) if age else None


def _starts_at(day: str, month_label: str, time_label: str, today: date) -> str:
    month_word = month_label.lower().split(",", 1)[0].strip()
    month = MONTHS[month_word]
    year = today.year + (1 if month < today.month else 0)
    value = datetime(year, month, int(day), *map(int, time_label.split(":")))
    return value.isoformat(timespec="minutes")


def parse_affiche(html_text: str, today: date | None = None) -> list[AfficheItem]:
    parser = _AfficheParser()
    parser.feed(html_text)
    reference_date = today or date.today()
    result: list[AfficheItem] = []

    for raw in parser.items:
        required = (raw.get("day"), raw.get("month"), raw.get("time"))
        if not all(required):
            continue
        genre, age_rating = _split_genre_and_age(raw.get("subgenre"))
        result.append(
            AfficheItem(
                title=raw["title"],
                play_url=urljoin(BASE_URL, raw["play_url"]),
                starts_at=_starts_at(*required, reference_date),
                genre=genre,
                age_rating=age_rating,
                duration=raw.get("duration"),
                venue=raw.get("venue"),
                image_url=urljoin(BASE_URL, raw["image_url"]) if raw.get("image_url") else None,
                ticket_event_id=raw.get("ticket_event_id"),
            )
        )
    return result


def preview_json(items: list[AfficheItem]) -> str:
    return json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2)
