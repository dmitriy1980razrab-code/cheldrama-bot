from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import sqlite3
import re
from difflib import SequenceMatcher


@dataclass(frozen=True)
class Performance:
    title: str
    starts_at: datetime
    venue: str | None
    genre: str | None
    age_rating: str | None
    play_url: str
    ticket_event_id: str | None


@dataclass(frozen=True)
class Play:
    id: int
    title: str
    director: str | None
    summary: str | None
    duration_minutes: int | None
    age_rating: str | None


@dataclass(frozen=True)
class Artist:
    id: int
    full_name: str


def _normalize(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    return " ".join(re.findall(r"[a-zа-я0-9]+", value))


def _best_phrase_similarity(text: str, candidate: str) -> float:
    text_words = text.split()
    candidate_words = candidate.split()
    size = len(candidate_words)
    phrases = [
        " ".join(text_words[start:start + width])
        for width in {max(1, size - 1), size, size + 1}
        for start in range(max(0, len(text_words) - width + 1))
    ]
    return max((SequenceMatcher(None, phrase, candidate).ratio() for phrase in phrases), default=0.0)


def _surname_matches(surname: str, words: set[str]) -> bool:
    for word in words:
        if word == surname:
            return True
        if word.startswith(surname) and word[len(surname):] in {"а", "у", "ом", "ым", "е", "ой"}:
            return True
        if surname.endswith(("а", "я")):
            stem = surname[:-1]
            if word.startswith(stem) and word[len(stem):] in {"ой", "ей", "ую", "ю", "е"}:
                return True
    return False


def find_play_in_text(connection: sqlite3.Connection, text: str) -> Play | None:
    normalized_text = _normalize(text)
    rows = connection.execute(
        """
        SELECT id, title, normalized_title, director, summary, duration_minutes, age_rating
        FROM plays WHERE is_active = 1
        ORDER BY length(normalized_title) DESC
        """
    ).fetchall()
    for row in rows:
        if row["normalized_title"] in normalized_text:
            return Play(
                row["id"], row["title"], row["director"], row["summary"],
                row["duration_minutes"],
                row["age_rating"],
            )
    fuzzy_matches = [
        (_best_phrase_similarity(normalized_text, row["normalized_title"]), row)
        for row in rows
    ]
    score, row = max(fuzzy_matches, default=(0.0, None), key=lambda item: item[0])
    if row is not None and score >= 0.82:
        return Play(
            row["id"], row["title"], row["director"], row["summary"],
            row["duration_minutes"],
            row["age_rating"],
        )
    return None


def upcoming_for_play(
    connection: sqlite3.Connection,
    play_id: int,
    from_time: datetime,
    limit: int = 3,
) -> list[Performance]:
    rows = connection.execute(
        """
        SELECT p.title, p.genre, p.age_rating, p.source_url AS play_url,
               e.starts_at, e.venue, e.ticket_event_id
        FROM performances e
        JOIN plays p ON p.id = e.play_id
        WHERE p.id = ? AND e.status = 'scheduled' AND e.starts_at >= ?
        ORDER BY e.starts_at
        LIMIT ?
        """,
        (play_id, from_time.isoformat(timespec="minutes"), limit),
    ).fetchall()
    return _rows_to_performances(rows)


def venues_for_play(
    connection: sqlite3.Connection,
    play_id: int,
    from_time: datetime,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT e.venue
        FROM performances e
        WHERE e.play_id = ? AND e.status = 'scheduled'
          AND e.starts_at >= ? AND e.venue IS NOT NULL AND trim(e.venue) != ''
        ORDER BY e.venue
        """,
        (play_id, from_time.isoformat(timespec="minutes")),
    ).fetchall()
    return [row["venue"] for row in rows]


def upcoming_by_age(
    connection: sqlite3.Connection,
    viewer_age: int,
    from_time: datetime,
    limit: int = 10,
) -> list[Performance]:
    rows = connection.execute(
        """
        SELECT p.title, p.genre, p.age_rating, p.source_url AS play_url,
               e.starts_at, e.venue, e.ticket_event_id
        FROM performances e
        JOIN plays p ON p.id = e.play_id
        WHERE e.status = 'scheduled' AND e.starts_at >= ?
          AND p.age_rating IS NOT NULL
          AND CAST(replace(p.age_rating, '+', '') AS INTEGER) <= ?
        ORDER BY e.starts_at, p.title
        LIMIT ?
        """,
        (from_time.isoformat(timespec="minutes"), viewer_age, limit),
    ).fetchall()
    return _rows_to_performances(rows)


def cast_for_play(connection: sqlite3.Connection, play_id: int) -> list[tuple[str | None, str]]:
    rows = connection.execute(
        """
        SELECT r.role_name, a.full_name
        FROM roles r JOIN artists a ON a.id = r.artist_id
        WHERE r.play_id = ? AND a.is_active = 1
        ORDER BY r.rowid, a.full_name
        """,
        (play_id,),
    ).fetchall()
    return [(row["role_name"], row["full_name"]) for row in rows]


def find_artists_in_text(connection: sqlite3.Connection, text: str) -> list[Artist]:
    normalized_text = _normalize(text)
    words = set(re.findall(r"[a-zа-я0-9-]+", normalized_text))
    fuzzy_words = words - {
        "где", "играет", "участвует", "когда", "ближайший", "ближайшие",
        "спектакль", "спектакли", "артист", "актриса", "актер", "актёр",
        "роль", "роли", "покажи", "подскажи", "какие", "какой", "есть",
    }
    rows = connection.execute(
        "SELECT id, full_name, normalized_name FROM artists WHERE is_active = 1 ORDER BY full_name"
    ).fetchall()
    exact: list[Artist] = []
    surname_matches: list[Artist] = []
    for row in rows:
        artist = Artist(row["id"], row["full_name"])
        normalized_name = row["normalized_name"]
        if normalized_name in normalized_text:
            exact.append(artist)
            continue
        surname = normalized_name.split()[-1]
        if _surname_matches(surname, words):
            surname_matches.append(artist)
            continue
        if any(
            len(word) >= 4 and SequenceMatcher(None, word, surname).ratio() >= 0.80
            for word in fuzzy_words
        ):
            surname_matches.append(artist)
    return exact or surname_matches


def artist_repertoire(
    connection: sqlite3.Connection,
    artist_id: int,
) -> list[tuple[str, str | None]]:
    rows = connection.execute(
        """
        SELECT DISTINCT p.title, r.role_name
        FROM roles r JOIN plays p ON p.id = r.play_id
        WHERE r.artist_id = ? AND p.is_active = 1
        ORDER BY p.title, r.role_name
        """,
        (artist_id,),
    ).fetchall()
    return [(row["title"], row["role_name"]) for row in rows]


def upcoming_for_artist(
    connection: sqlite3.Connection,
    artist_id: int,
    from_time: datetime,
    limit: int = 3,
) -> list[tuple[Performance, str | None]]:
    rows = connection.execute(
        """
        SELECT DISTINCT p.title, p.genre, p.age_rating, p.source_url AS play_url,
               e.starts_at, e.venue, e.ticket_event_id, r.role_name
        FROM roles r
        JOIN plays p ON p.id = r.play_id
        JOIN performances e ON e.play_id = p.id
        WHERE r.artist_id = ? AND p.is_active = 1
          AND e.status = 'scheduled' AND e.starts_at >= ?
        ORDER BY e.starts_at, p.title
        LIMIT ?
        """,
        (artist_id, from_time.isoformat(timespec="minutes"), limit),
    ).fetchall()
    result: list[tuple[Performance, str | None]] = []
    for row in rows:
        performance = Performance(
            title=row["title"],
            starts_at=datetime.fromisoformat(row["starts_at"]),
            venue=row["venue"],
            genre=row["genre"],
            age_rating=row["age_rating"],
            play_url=row["play_url"],
            ticket_event_id=row["ticket_event_id"],
        )
        result.append((performance, row["role_name"]))
    return result


def plays_by_genre(
    connection: sqlite3.Connection,
    genre_fragment: str,
    limit: int = 20,
) -> list[tuple[str, str]]:
    fragment = _normalize(genre_fragment)
    rows = connection.execute(
        """
        SELECT title, genre FROM plays
        WHERE is_active = 1 AND genre IS NOT NULL
        ORDER BY title
        """
    ).fetchall()
    result = [
        (row["title"], row["genre"])
        for row in rows
        if fragment in _normalize(row["genre"])
    ]
    return result[:limit]


def new_year_performances(
    connection: sqlite3.Connection,
    first_day: date,
    last_day: date,
) -> list[Performance]:
    start = datetime.combine(first_day, time.min)
    end = datetime.combine(last_day + timedelta(days=1), time.min)
    rows = connection.execute(
        """
        SELECT p.title, p.genre, p.age_rating, p.source_url AS play_url,
               e.starts_at, e.venue, e.ticket_event_id
        FROM performances e
        JOIN plays p ON p.id = e.play_id
        WHERE e.status = 'scheduled' AND e.starts_at >= ? AND e.starts_at < ?
          AND (
            p.catalog_kind = 'children'
            OR replace(lower(COALESCE(p.genre, '')), 'ё', 'е') LIKE '%сказк%'
          )
        ORDER BY e.starts_at, p.title
        """,
        (start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")),
    ).fetchall()
    return _rows_to_performances(rows)


def _rows_to_performances(rows: list[sqlite3.Row]) -> list[Performance]:
    return [
        Performance(
            title=row["title"],
            starts_at=datetime.fromisoformat(row["starts_at"]),
            venue=row["venue"],
            genre=row["genre"],
            age_rating=row["age_rating"],
            play_url=row["play_url"],
            ticket_event_id=row["ticket_event_id"],
        )
        for row in rows
    ]


def nearest(
    connection: sqlite3.Connection,
    from_time: datetime,
    limit: int = 3,
) -> list[Performance]:
    rows = connection.execute(
        """
        SELECT p.title, p.genre, p.age_rating, p.source_url AS play_url,
               e.starts_at, e.venue, e.ticket_event_id
        FROM performances e
        JOIN plays p ON p.id = e.play_id
        WHERE e.status = 'scheduled' AND e.starts_at >= ?
        ORDER BY e.starts_at, p.title
        LIMIT ?
        """,
        (from_time.isoformat(timespec="minutes"), limit),
    ).fetchall()
    return _rows_to_performances(rows)


def between_dates(
    connection: sqlite3.Connection,
    first_day: date,
    last_day: date,
) -> list[Performance]:
    start = datetime.combine(first_day, time.min)
    end = datetime.combine(last_day + timedelta(days=1), time.min)
    rows = connection.execute(
        """
        SELECT p.title, p.genre, p.age_rating, p.source_url AS play_url,
               e.starts_at, e.venue, e.ticket_event_id
        FROM performances e
        JOIN plays p ON p.id = e.play_id
        WHERE e.status = 'scheduled' AND e.starts_at >= ? AND e.starts_at < ?
        ORDER BY e.starts_at, p.title
        """,
        (start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")),
    ).fetchall()
    return _rows_to_performances(rows)
