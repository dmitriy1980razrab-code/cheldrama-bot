from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import sqlite3

from theatre_bot.site_affiche import AfficheItem


@dataclass(frozen=True)
class SyncReport:
    plays_added: int = 0
    performances_added: int = 0
    performances_updated: int = 0
    performances_unchanged: int = 0


def connect(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    connection.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _source_key(item: AfficheItem) -> str:
    if item.ticket_event_id:
        return f"kassy:{item.ticket_event_id}"
    raw = f"{item.play_url}|{item.starts_at}|{item.venue or ''}"
    return "site:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def sync_affiche(connection: sqlite3.Connection, items: list[AfficheItem]) -> SyncReport:
    plays_added = 0
    performances_added = 0
    performances_updated = 0
    performances_unchanged = 0
    synced_at = _now()

    with connection:
        for item in items:
            play = connection.execute(
                "SELECT id FROM plays WHERE source_url = ?",
                (item.play_url,),
            ).fetchone()

            if play is None:
                cursor = connection.execute(
                    """
                    INSERT INTO plays (
                        source_url, title, normalized_title, genre, age_rating,
                        image_url, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.play_url,
                        item.title,
                        _normalize(item.title),
                        item.genre,
                        item.age_rating,
                        item.image_url,
                        synced_at,
                    ),
                )
                play_id = cursor.lastrowid
                plays_added += 1
            else:
                play_id = play["id"]
                connection.execute(
                    """
                    UPDATE plays
                    SET title = ?, normalized_title = ?, genre = ?, age_rating = ?,
                        image_url = ?, is_active = 1, synced_at = ?
                    WHERE id = ?
                    """,
                    (
                        item.title,
                        _normalize(item.title),
                        item.genre,
                        item.age_rating,
                        item.image_url,
                        synced_at,
                        play_id,
                    ),
                )

            source_key = _source_key(item)
            performance = connection.execute(
                """
                SELECT starts_at, venue, ticket_event_id, status
                FROM performances WHERE source_key = ?
                """,
                (source_key,),
            ).fetchone()
            values = (item.starts_at, item.venue, item.ticket_event_id, "scheduled")

            if performance is None:
                connection.execute(
                    """
                    INSERT INTO performances (
                        play_id, starts_at, venue, ticket_event_id, status,
                        source_key, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        play_id,
                        item.starts_at,
                        item.venue,
                        item.ticket_event_id,
                        "scheduled",
                        source_key,
                        synced_at,
                    ),
                )
                performances_added += 1
            elif tuple(performance) != values:
                connection.execute(
                    """
                    UPDATE performances
                    SET play_id = ?, starts_at = ?, venue = ?, ticket_event_id = ?,
                        status = 'scheduled', synced_at = ?
                    WHERE source_key = ?
                    """,
                    (
                        play_id,
                        item.starts_at,
                        item.venue,
                        item.ticket_event_id,
                        synced_at,
                        source_key,
                    ),
                )
                performances_updated += 1
            else:
                performances_unchanged += 1

    return SyncReport(
        plays_added=plays_added,
        performances_added=performances_added,
        performances_updated=performances_updated,
        performances_unchanged=performances_unchanged,
    )

