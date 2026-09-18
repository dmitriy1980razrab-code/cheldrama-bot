from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import sqlite3


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


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def find_play_in_text(connection: sqlite3.Connection, text: str) -> Play | None:
    normalized_text = _normalize(text)
    rows = connection.execute(
        """
        SELECT id, title, normalized_title, director, summary
        FROM plays WHERE is_active = 1
        ORDER BY length(normalized_title) DESC
        """
    ).fetchall()
    for row in rows:
        if row["normalized_title"] in normalized_text:
            return Play(row["id"], row["title"], row["director"], row["summary"])
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
