from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import sqlite3

from theatre_bot.site_affiche import AfficheItem
from theatre_bot.site_play import PlayDetails


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
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(plays)").fetchall()
    }
    if "catalog_kind" not in columns:
        connection.execute(
            "ALTER TABLE plays ADD COLUMN catalog_kind TEXT NOT NULL DEFAULT 'affiche'"
        )
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


def play_sources(
    connection: sqlite3.Connection,
    missing_details_only: bool = False,
) -> list[tuple[int, str]]:
    condition = "AND (summary IS NULL OR genre IS NULL)" if missing_details_only else ""
    rows = connection.execute(
        f"SELECT id, source_url FROM plays WHERE is_active = 1 {condition} ORDER BY id"
    ).fetchall()
    return [(row["id"], row["source_url"]) for row in rows]


def save_play_details(
    connection: sqlite3.Connection,
    play_id: int,
    details: PlayDetails,
) -> None:
    synced_at = _now()
    with connection:
        connection.execute(
            """
            UPDATE plays
            SET director = ?, summary = ?, genre = COALESCE(?, genre),
                age_rating = COALESCE(?, age_rating), synced_at = ?
            WHERE id = ?
            """,
            (
                details.director,
                details.summary,
                details.genre,
                details.age_rating,
                synced_at,
                play_id,
            ),
        )
        connection.execute("DELETE FROM roles WHERE play_id = ?", (play_id,))
        for member in details.cast:
            connection.execute(
                """
                INSERT INTO artists (
                    source_url, full_name, normalized_name, synced_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(source_url) DO UPDATE SET
                    full_name = excluded.full_name,
                    normalized_name = excluded.normalized_name,
                    is_active = 1,
                    synced_at = excluded.synced_at
                """,
                (
                    member.artist_url,
                    member.artist_name,
                    _normalize(member.artist_name),
                    synced_at,
                ),
            )
            artist_id = connection.execute(
                "SELECT id FROM artists WHERE source_url = ?",
                (member.artist_url,),
            ).fetchone()["id"]
            connection.execute(
                "INSERT OR IGNORE INTO roles (play_id, artist_id, role_name) VALUES (?, ?, ?)",
                (play_id, artist_id, member.role_name),
            )


def sync_repertoire(connection: sqlite3.Connection, items) -> tuple[int, int, int]:
    added = 0
    updated = 0
    deactivated = 0
    synced_at = _now()
    seen_urls = {item.play_url for item in items}

    with connection:
        for item in items:
            existing = connection.execute(
                "SELECT id, title, catalog_kind, is_active FROM plays WHERE source_url = ?",
                (item.play_url,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO plays (
                        source_url, title, normalized_title, catalog_kind, is_active, synced_at
                    ) VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        item.play_url,
                        item.title,
                        _normalize(item.title),
                        item.catalog_kind,
                        synced_at,
                    ),
                )
                added += 1
            else:
                changed = (
                    existing["title"] != item.title
                    or existing["catalog_kind"] != item.catalog_kind
                    or existing["is_active"] != 1
                )
                connection.execute(
                    """
                    UPDATE plays SET title = ?, normalized_title = ?, catalog_kind = ?,
                                     is_active = 1, synced_at = ?
                    WHERE id = ?
                    """,
                    (
                        item.title,
                        _normalize(item.title),
                        item.catalog_kind,
                        synced_at,
                        existing["id"],
                    ),
                )
                updated += int(changed)

        active_catalog = connection.execute(
            "SELECT id, source_url FROM plays WHERE catalog_kind IN ('repertoire', 'children') AND is_active = 1"
        ).fetchall()
        for row in active_catalog:
            if row["source_url"] not in seen_urls:
                connection.execute("UPDATE plays SET is_active = 0 WHERE id = ?", (row["id"],))
                deactivated += 1

    return added, updated, deactivated
