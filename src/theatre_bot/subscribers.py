from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import hmac
import os
import sqlite3

from cryptography.fernet import Fernet, InvalidToken


ALLOWED_CHANNELS = {"vk", "max"}
REQUIRED_SERVICE_CONSENTS = ("personal_data", "service_notifications")


@dataclass(frozen=True)
class SubscriberStats:
    active_by_channel: dict[str, int]
    active_play_subscriptions: list[tuple[str, int]]
    preferences: list[tuple[str, str, int]]
    marketing_consents: int


@dataclass(frozen=True)
class SubscriberProfile:
    subscriber_id: int
    channel: str
    external_id: str
    display_name: str | None
    status: str
    subscribed_at: str | None
    unsubscribed_at: str | None
    subscriptions: tuple[tuple[str, str], ...]
    preferences: tuple[tuple[str, str], ...]
    consents: dict[str, bool]


class IdentityProtector:
    def __init__(self, encryption_key: str | bytes, hash_key: str | bytes):
        self._fernet = Fernet(
            encryption_key.encode("ascii") if isinstance(encryption_key, str) else encryption_key
        )
        self._hash_key = hash_key.encode("utf-8") if isinstance(hash_key, str) else hash_key
        if len(self._hash_key) < 32:
            raise ValueError("subscriber hash key must contain at least 32 bytes")

    @classmethod
    def from_environment(cls) -> "IdentityProtector":
        encryption_key = _secret_value(
            "THEATRE_SUBSCRIBER_ENCRYPTION_KEY",
            "THEATRE_SUBSCRIBER_ENCRYPTION_KEY_FILE",
        )
        hash_key = _secret_value(
            "THEATRE_SUBSCRIBER_HASH_KEY",
            "THEATRE_SUBSCRIBER_HASH_KEY_FILE",
        )
        if not encryption_key or not hash_key:
            raise RuntimeError("subscriber protection keys are not configured")
        return cls(encryption_key, hash_key)

    def lookup_hash(self, channel: str, external_id: str) -> str:
        _validate_channel(channel)
        value = f"{channel}:{external_id}".encode("utf-8")
        return hmac.new(self._hash_key, value, hashlib.sha256).hexdigest()

    def encrypt(self, external_id: str) -> bytes:
        return self._fernet.encrypt(external_id.encode("utf-8"))

    def decrypt(self, encrypted: bytes) -> str:
        try:
            return self._fernet.decrypt(encrypted).decode("utf-8")
        except InvalidToken as error:
            raise ValueError("subscriber identifier cannot be decrypted") from error


def _validate_channel(channel: str) -> None:
    if channel not in ALLOWED_CHANNELS:
        raise ValueError("subscriptions are available only in VK or MAX")


def _secret_value(variable: str, file_variable: str) -> str | None:
    secret_file = os.environ.get(file_variable)
    if secret_file:
        return Path(secret_file).read_text(encoding="utf-8").strip()
    return os.environ.get(variable)


def _timestamp(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect_subscribers(path: str | Path) -> sqlite3.Connection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_subscribers(connection: sqlite3.Connection) -> None:
    schema = Path(__file__).with_name("subscriber_schema.sql")
    connection.executescript(schema.read_text(encoding="utf-8"))
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(subscribers)").fetchall()
    }
    if "display_name_encrypted" not in columns:
        connection.execute(
            "ALTER TABLE subscribers ADD COLUMN display_name_encrypted BLOB"
        )
    queue_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(notification_queue)").fetchall()
    }
    queue_migrations = {
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "last_attempt_at": "TEXT",
        "next_attempt_at": "TEXT",
    }
    for column, definition in queue_migrations.items():
        if column not in queue_columns:
            connection.execute(
                f"ALTER TABLE notification_queue ADD COLUMN {column} {definition}"
            )
    connection.commit()


def _get_or_create_subscriber(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    channel: str,
    external_id: str,
    at: datetime | None = None,
    display_name: str | None = None,
) -> int:
    _validate_channel(channel)
    identity_hash = protector.lookup_hash(channel, external_id)
    row = connection.execute(
        "SELECT id FROM subscribers WHERE channel = ? AND external_id_hash = ?",
        (channel, identity_hash),
    ).fetchone()
    timestamp = _timestamp(at)
    encrypted = protector.encrypt(external_id)
    encrypted_name = protector.encrypt(display_name.strip()) if display_name and display_name.strip() else None
    if row:
        connection.execute(
            """
            UPDATE subscribers SET external_id_encrypted = ?,
                display_name_encrypted = COALESCE(?, display_name_encrypted), updated_at = ?
            WHERE id = ?
            """,
            (encrypted, encrypted_name, timestamp, row["id"]),
        )
        return row["id"]
    cursor = connection.execute(
        """
        INSERT INTO subscribers (
            channel, external_id_hash, external_id_encrypted,
            display_name_encrypted, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (channel, identity_hash, encrypted, encrypted_name, timestamp, timestamp),
    )
    return cursor.lastrowid


def record_consent(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    channel: str,
    external_id: str,
    consent_type: str,
    document_version: str,
    granted: bool,
    source: str,
    at: datetime | None = None,
    display_name: str | None = None,
) -> int:
    if consent_type not in {"personal_data", "service_notifications", "marketing"}:
        raise ValueError("unknown consent type")
    if not document_version.strip():
        raise ValueError("document version is required")
    timestamp = _timestamp(at)
    with connection:
        subscriber_id = _get_or_create_subscriber(
            connection, protector, channel, external_id, at, display_name
        )
        connection.execute(
            """
            INSERT INTO consent_events (
                subscriber_id, consent_type, action, document_version, source, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                subscriber_id,
                consent_type,
                "granted" if granted else "revoked",
                document_version,
                source,
                timestamp,
            ),
        )
    return subscriber_id


def _has_current_consent(
    connection: sqlite3.Connection,
    subscriber_id: int,
    consent_type: str,
) -> bool:
    row = connection.execute(
        """
        SELECT action FROM consent_events
        WHERE subscriber_id = ? AND consent_type = ?
        ORDER BY id DESC LIMIT 1
        """,
        (subscriber_id, consent_type),
    ).fetchone()
    return bool(row and row["action"] == "granted")


def subscribe_to_play(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    channel: str,
    external_id: str,
    play_key: str,
    play_title: str,
    at: datetime | None = None,
) -> int:
    timestamp = _timestamp(at)
    with connection:
        subscriber_id = _get_or_create_subscriber(
            connection, protector, channel, external_id, at
        )
        missing = [
            consent
            for consent in REQUIRED_SERVICE_CONSENTS
            if not _has_current_consent(connection, subscriber_id, consent)
        ]
        if missing:
            raise PermissionError("required consents are missing")
        connection.execute(
            """
            UPDATE subscribers
            SET status = 'active', subscribed_at = ?, unsubscribed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, subscriber_id),
        )
        connection.execute(
            """
            INSERT INTO subscriptions (
                subscriber_id, topic_type, topic_key, topic_label,
                notify_changes, remind_24h, status, subscribed_at, unsubscribed_at
            ) VALUES (?, 'play', ?, ?, 1, 1, 'active', ?, NULL)
            ON CONFLICT(subscriber_id, topic_type, topic_key) DO UPDATE SET
                topic_label = excluded.topic_label,
                notify_changes = 1,
                remind_24h = 1,
                status = 'active',
                subscribed_at = excluded.subscribed_at,
                unsubscribed_at = NULL
            """,
            (subscriber_id, play_key, play_title, timestamp),
        )
        connection.execute(
            """
            INSERT INTO preferences (
                subscriber_id, preference_type, preference_key,
                preference_label, source, recorded_at
            ) VALUES (?, 'play', ?, ?, 'subscription', ?)
            ON CONFLICT(subscriber_id, preference_type, preference_key) DO UPDATE SET
                preference_label = excluded.preference_label,
                source = excluded.source,
                recorded_at = excluded.recorded_at
            """,
            (subscriber_id, play_key, play_title, timestamp),
        )
        row = connection.execute(
            """
            SELECT id FROM subscriptions
            WHERE subscriber_id = ? AND topic_type = 'play' AND topic_key = ?
            """,
            (subscriber_id, play_key),
        ).fetchone()
    return row["id"]


def unsubscribe(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    channel: str,
    external_id: str,
    document_version: str,
    source: str,
    at: datetime | None = None,
) -> bool:
    identity_hash = protector.lookup_hash(channel, external_id)
    row = connection.execute(
        "SELECT id, status FROM subscribers WHERE channel = ? AND external_id_hash = ?",
        (channel, identity_hash),
    ).fetchone()
    if not row or row["status"] != "active":
        return False
    timestamp = _timestamp(at)
    with connection:
        connection.execute(
            """
            UPDATE subscribers
            SET status = 'unsubscribed', external_id_encrypted = NULL,
                display_name_encrypted = NULL,
                unsubscribed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, row["id"]),
        )
        connection.execute(
            """
            UPDATE subscriptions
            SET status = 'unsubscribed', unsubscribed_at = ?
            WHERE subscriber_id = ? AND status = 'active'
            """,
            (timestamp, row["id"]),
        )
        for consent_type in ("service_notifications", "marketing"):
            if _has_current_consent(connection, row["id"], consent_type):
                connection.execute(
                    """
                    INSERT INTO consent_events (
                        subscriber_id, consent_type, action,
                        document_version, source, recorded_at
                    ) VALUES (?, ?, 'revoked', ?, ?, ?)
                    """,
                    (row["id"], consent_type, document_version, source, timestamp),
                )
    return True


def available_subscription_actions(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    channel: str,
    external_id: str,
) -> tuple[str, ...]:
    identity_hash = protector.lookup_hash(channel, external_id)
    row = connection.execute(
        "SELECT status FROM subscribers WHERE channel = ? AND external_id_hash = ?",
        (channel, identity_hash),
    ).fetchone()
    if row and row["status"] == "active":
        return ("configure", "unsubscribe")
    return ("subscribe",)


def configure_play_notifications(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    channel: str,
    external_id: str,
    play_key: str,
    notify_changes: bool,
    remind_24h: bool,
) -> bool:
    if not notify_changes and not remind_24h:
        raise ValueError("at least one notification type is required")
    identity_hash = protector.lookup_hash(channel, external_id)
    with connection:
        cursor = connection.execute(
            """
            UPDATE subscriptions
            SET notify_changes = ?, remind_24h = ?
            WHERE subscriber_id = (
                SELECT id FROM subscribers
                WHERE channel = ? AND external_id_hash = ? AND status = 'active'
            ) AND topic_type = 'play' AND topic_key = ? AND status = 'active'
            """,
            (
                int(notify_changes), int(remind_24h),
                channel, identity_hash, play_key,
            ),
        )
    return cursor.rowcount == 1


def subscriber_stats(connection: sqlite3.Connection) -> SubscriberStats:
    active_by_channel = {
        row["channel"]: row["count"]
        for row in connection.execute(
            """
            SELECT channel, count(*) AS count FROM subscribers
            WHERE status = 'active' GROUP BY channel ORDER BY channel
            """
        ).fetchall()
    }
    play_subscriptions = [
        (row["topic_label"], row["count"])
        for row in connection.execute(
            """
            SELECT topic_label, count(*) AS count FROM subscriptions
            WHERE status = 'active' AND topic_type = 'play'
            GROUP BY topic_key, topic_label ORDER BY count DESC, topic_label
            """
        ).fetchall()
    ]
    preferences = [
        (row["preference_type"], row["preference_label"], row["count"])
        for row in connection.execute(
            """
            SELECT preference_type, preference_label, count(*) AS count
            FROM preferences GROUP BY preference_type, preference_key, preference_label
            ORDER BY count DESC, preference_label
            """
        ).fetchall()
    ]
    marketing = connection.execute(
        """
        SELECT count(*) FROM subscribers s
        WHERE s.status = 'active' AND (
            SELECT action FROM consent_events c
            WHERE c.subscriber_id = s.id AND c.consent_type = 'marketing'
            ORDER BY c.id DESC LIMIT 1
        ) = 'granted'
        """
    ).fetchone()[0]
    return SubscriberStats(active_by_channel, play_subscriptions, preferences, marketing)


def _validate_admin_access(admin_actor: str, purpose: str) -> None:
    if not admin_actor.strip():
        raise PermissionError("authorized administrator is required")
    if not purpose.strip():
        raise ValueError("access purpose is required")


def _current_consents(connection: sqlite3.Connection, subscriber_id: int) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for consent_type in ("personal_data", "service_notifications", "marketing"):
        result[consent_type] = _has_current_consent(
            connection, subscriber_id, consent_type
        )
    return result


def _build_profile(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    row: sqlite3.Row,
) -> SubscriberProfile:
    if row["external_id_encrypted"] is None:
        external_id = ""
    else:
        external_id = protector.decrypt(row["external_id_encrypted"])
    display_name = (
        protector.decrypt(row["display_name_encrypted"])
        if row["display_name_encrypted"] is not None
        else None
    )
    subscriptions = tuple(
        (item["topic_type"], item["topic_label"])
        for item in connection.execute(
            """
            SELECT topic_type, topic_label FROM subscriptions
            WHERE subscriber_id = ? AND status = 'active'
            ORDER BY topic_type, topic_label
            """,
            (row["id"],),
        ).fetchall()
    )
    preferences = tuple(
        (item["preference_type"], item["preference_label"])
        for item in connection.execute(
            """
            SELECT preference_type, preference_label FROM preferences
            WHERE subscriber_id = ? ORDER BY recorded_at DESC, id DESC
            """,
            (row["id"],),
        ).fetchall()
    )
    return SubscriberProfile(
        subscriber_id=row["id"],
        channel=row["channel"],
        external_id=external_id,
        display_name=display_name,
        status=row["status"],
        subscribed_at=row["subscribed_at"],
        unsubscribed_at=row["unsubscribed_at"],
        subscriptions=subscriptions,
        preferences=preferences,
        consents=_current_consents(connection, row["id"]),
    )


def subscriber_profile(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    subscriber_id: int,
    admin_actor: str,
    purpose: str,
    at: datetime | None = None,
) -> SubscriberProfile | None:
    _validate_admin_access(admin_actor, purpose)
    row = connection.execute(
        "SELECT * FROM subscribers WHERE id = ?", (subscriber_id,)
    ).fetchone()
    if row is None:
        return None
    profile = _build_profile(connection, protector, row)
    with connection:
        connection.execute(
            """
            INSERT INTO subscriber_access_log (
                subscriber_id, admin_actor, action, purpose, accessed_at
            ) VALUES (?, ?, 'view_profile', ?, ?)
            """,
            (subscriber_id, admin_actor.strip(), purpose.strip(), _timestamp(at)),
        )
    return profile


def list_active_subscriber_profiles(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    admin_actor: str,
    purpose: str,
    at: datetime | None = None,
) -> tuple[SubscriberProfile, ...]:
    _validate_admin_access(admin_actor, purpose)
    rows = connection.execute(
        "SELECT * FROM subscribers WHERE status = 'active' ORDER BY id"
    ).fetchall()
    profiles = tuple(_build_profile(connection, protector, row) for row in rows)
    with connection:
        connection.execute(
            """
            INSERT INTO subscriber_access_log (
                subscriber_id, admin_actor, action, purpose, accessed_at
            ) VALUES (NULL, ?, 'list_profiles', ?, ?)
            """,
            (admin_actor.strip(), purpose.strip(), _timestamp(at)),
        )
    return profiles
