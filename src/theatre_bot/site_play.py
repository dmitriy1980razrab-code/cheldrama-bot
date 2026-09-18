from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from theatre_bot.site_affiche import BASE_URL


@dataclass(frozen=True)
class CastMember:
    role_name: str | None
    artist_name: str
    artist_url: str


@dataclass(frozen=True)
class PlayDetails:
    title: str
    director: str | None
    summary: str | None
    cast: tuple[CastMember, ...]
    genre: str | None = None
    age_rating: str | None = None


def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
    return set((dict(attrs).get("class") or "").split())


class _PlayParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.staff_depth: int | None = None
        self.summary_depth: int | None = None
        self.title: str | None = None
        self.director: str | None = None
        self.summary_parts: list[str] = []
        self.cast: list[CastMember] = []
        self.capture: str | None = None
        self.buffer: list[str] = []
        self.pending_credit: str | None = None
        self.paragraph_people: list[tuple[str, str]] = []
        self.person_href: str | None = None
        self.person_buffer: list[str] = []
        self.skip_summary_paragraph = False
        self.detail_depth: int | None = None
        self.points: list[str] = []

    @property
    def in_staff(self) -> bool:
        return self.staff_depth is not None

    @property
    def in_summary(self) -> bool:
        return self.summary_depth is not None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = _classes(attrs)
        if tag == "div":
            self.div_depth += 1
            if "play-staff" in classes:
                self.staff_depth = self.div_depth
            elif "text" in classes and not self.in_staff and self.summary_depth is None:
                self.summary_depth = self.div_depth
            elif "detail" in classes:
                self.detail_depth = self.div_depth
            elif "point" in classes and self.detail_depth is not None:
                self.capture, self.buffer = "detail_point", []

        if tag == "h1" and self.title is None:
            self.capture, self.buffer = "title", []
        elif tag == "dt" and not self.in_staff:
            self.capture, self.buffer = "credit", []
        elif tag == "dd" and not self.in_staff:
            self.capture, self.buffer = "credit_role", []
        elif tag == "p" and self.in_staff:
            self.capture, self.buffer = "cast_paragraph", []
            self.paragraph_people = []
        elif tag == "p" and self.in_summary:
            self.capture, self.buffer = "summary_paragraph", []
            style = attributes.get("style") or ""
            self.skip_summary_paragraph = "text-align: right" in style

        href = attributes.get("href") or ""
        if tag == "a" and self.in_staff and "/person/" in href:
            self.person_href = href
            self.person_buffer = []

    def handle_endtag(self, tag: str) -> None:
        text = " ".join(" ".join(self.buffer).split())
        if tag == "h1" and self.capture == "title":
            self.title = text
            self.capture = None
        elif tag == "dt" and self.capture == "credit":
            self.pending_credit = text
            self.capture = None
        elif tag == "dd" and self.capture == "credit_role":
            if "режиссер-постановщик" in text.casefold() and self.pending_credit:
                self.director = self.pending_credit
            self.capture = None
        elif tag == "a" and self.person_href:
            name = " ".join(" ".join(self.person_buffer).split())
            if name:
                self.paragraph_people.append((name, urljoin(BASE_URL, self.person_href)))
            self.person_href = None
            self.person_buffer = []
        elif tag == "p" and self.capture == "summary_paragraph":
            if text and not self.skip_summary_paragraph:
                self.summary_parts.append(text)
            self.capture = None
            self.skip_summary_paragraph = False
        elif tag == "p" and self.capture == "cast_paragraph":
            role = text.split("-", 1)[0].strip() if "-" in text else None
            for name, url in self.paragraph_people:
                self.cast.append(CastMember(role or None, name, url))
            self.capture = None
            self.paragraph_people = []
        elif tag == "div" and self.capture == "detail_point":
            if text:
                self.points.append(text)
            self.capture = None

        if tag == "div":
            if self.staff_depth == self.div_depth:
                self.staff_depth = None
            if self.summary_depth == self.div_depth:
                self.summary_depth = None
            if self.detail_depth == self.div_depth:
                self.detail_depth = None
            self.div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.buffer.append(data)
        if self.person_href:
            self.person_buffer.append(data)


def fetch_play_html(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "cheldrama-bot/0.1 (+official-theatre-project)"})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_play(html_text: str) -> PlayDetails:
    parser = _PlayParser()
    parser.feed(html_text)
    if not parser.title:
        raise ValueError("На странице не найдено название спектакля")
    summary = " ".join(parser.summary_parts).strip() or None
    age_rating = next((point for point in parser.points if point.rstrip().endswith("+")), None)
    genre = next(
        (
            point
            for point in parser.points
            if point != age_rating
            and not point.casefold().startswith("премьера")
            and "час" not in point.casefold()
            and "минут" not in point.casefold()
        ),
        None,
    )
    return PlayDetails(
        title=parser.title,
        director=parser.director,
        summary=summary,
        cast=tuple(parser.cast),
        genre=genre,
        age_rating=age_rating,
    )
