from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sqlite3

from theatre_bot.subscribers import IdentityProtector


ALLOWED_CHANNELS = {"all", "vk", "max"}
ALLOWED_TARGETS = {"all", "play", "artist", "genre"}


@dataclass(frozen=True)
class CampaignRecipient:
    subscriber_id: int
    channel: str
    external_id: str
    display_name: str | None
    preferences: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CampaignPreview:
    campaign_id: int
    total: int
    by_channel: dict[str, int]
    recipients: tuple[CampaignRecipient, ...]


def _timestamp(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _require_actor(actor: str) -> str:
    value = actor.strip()
    if not value:
        raise PermissionError("authorized administrator is required")
    return value


def create_campaign(
    connection: sqlite3.Connection,
    name: str,
    message: str,
    created_by: str,
    channel: str = "all",
    target_type: str = "all",
    target_key: str | None = None,
    target_label: str | None = None,
    at: datetime | None = None,
    *, premieres_only: bool = False, targets=None,
) -> int:
    actor = _require_actor(created_by)
    if channel not in ALLOWED_CHANNELS:
        raise ValueError("unknown campaign channel")
    if target_type not in ALLOWED_TARGETS:
        raise ValueError("unknown campaign target")
    if target_type != "all" and not (target_key and target_key.strip()):
        raise ValueError("target key is required")
    if not name.strip() or not message.strip():
        raise ValueError("campaign name and message are required")
    if len(message) > 2000:
        raise ValueError("campaign message is too long")
    if targets is not None and (target_type != 'all' or not targets or len(targets)>500 or
        any(kind not in {'play','artist','genre'} or not key for kind,key,label in targets)):
        raise ValueError('invalid campaign targets')
    timestamp = _timestamp(at)
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO campaigns (
                name, message, channel, target_type, target_key, target_label,
                created_by, created_at, premieres_only
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name.strip(), message.strip(), channel, target_type,
                target_key.strip() if target_key else None,
                target_label.strip() if target_label else None,
                actor, timestamp, int(premieres_only),
            ),
        )
        connection.execute(
            """
            INSERT INTO campaign_events (
                campaign_id, actor, event_type, details, recorded_at
            ) VALUES (?, ?, 'created', NULL, ?)
            """,
            (cursor.lastrowid, actor, timestamp),
        )
        if targets:
            connection.executemany('INSERT INTO campaign_targets (campaign_id,target_type,target_key,target_label) VALUES (?,?,?,?)',
                [(cursor.lastrowid,kind,key,label) for kind,key,label in targets])
    return cursor.lastrowid


def _eligible_rows(
    connection: sqlite3.Connection,
    campaign: sqlite3.Row,
    at: datetime | None,
    min_interval_days: int,
) -> list[sqlite3.Row]:
    current = at or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = (current.astimezone(timezone.utc) - timedelta(days=min_interval_days)).isoformat(
        timespec="seconds"
    )
    local=current.astimezone(timezone(timedelta(hours=5))).date()
    season=local.year if local.month>=9 else local.year-1
    in_season=(local.month>=9 or (local.month,local.day)<=(7,10))
    query = """
        SELECT s.* FROM subscribers s
        WHERE s.status = 'active'
          AND s.external_id_encrypted IS NOT NULL
          AND (? = 'all' OR s.channel = ?)
          AND (? = 0 OR s.interested_in_premieres = 1)
          AND (
              SELECT action FROM consent_events c
              WHERE c.subscriber_id = s.id AND c.consent_type = 'marketing'
              ORDER BY c.id DESC LIMIT 1
          ) = 'granted'
          AND (
              ? = 'all' OR EXISTS (
                  SELECT 1 FROM preferences p
                  WHERE p.subscriber_id = s.id
                    AND p.preference_type = ? AND p.preference_key = ?
              )
          )
          AND (NOT EXISTS (SELECT 1 FROM campaign_targets ct WHERE ct.campaign_id=?) OR EXISTS (
              SELECT 1 FROM campaign_targets ct WHERE ct.campaign_id=? AND (
                  (ct.target_type!='play' AND EXISTS (SELECT 1 FROM preferences p WHERE p.subscriber_id=s.id
                    AND p.preference_type=ct.target_type AND p.preference_key=ct.target_key))
                  OR (ct.target_type='play' AND ?=1 AND EXISTS (
                    SELECT 1 FROM survey_play_interests sp WHERE sp.subscriber_id=s.id
                    AND sp.play_key=ct.target_key AND sp.season_start_year=?))
              )
          ))
          AND NOT EXISTS (
              SELECT 1 FROM campaign_recipients cr
              WHERE cr.subscriber_id = s.id AND cr.status = 'sent'
                AND cr.sent_at >= ?
          )
        ORDER BY s.id
    """
    return connection.execute(
        query,
        (
            campaign["channel"], campaign["channel"], int(campaign["premieres_only"]),
            campaign["target_type"], campaign["target_type"],
            campaign["target_key"], campaign['id'], campaign['id'], int(in_season), season, cutoff,
        ),
    ).fetchall()


def preview_campaign(
    connection: sqlite3.Connection,
    protector: IdentityProtector,
    campaign_id: int,
    admin_actor: str,
    purpose: str,
    at: datetime | None = None,
    min_interval_days: int = 7,
) -> CampaignPreview:
    actor = _require_actor(admin_actor)
    if not purpose.strip():
        raise ValueError("preview purpose is required")
    if min_interval_days < 0:
        raise ValueError("minimum interval cannot be negative")
    campaign = connection.execute(
        "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    if campaign is None:
        raise LookupError("campaign not found")
    if campaign["status"] != "draft":
        raise ValueError("only a draft campaign can be previewed")
    rows = _eligible_rows(connection, campaign, at, min_interval_days)
    recipients = []
    by_channel: dict[str, int] = {}
    for row in rows:
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
        recipients.append(
            CampaignRecipient(
                subscriber_id=row["id"],
                channel=row["channel"],
                external_id=protector.decrypt(row["external_id_encrypted"]),
                display_name=(
                    protector.decrypt(row["display_name_encrypted"])
                    if row["display_name_encrypted"] is not None else None
                ),
                preferences=preferences,
            )
        )
        by_channel[row["channel"]] = by_channel.get(row["channel"], 0) + 1
    timestamp = _timestamp(at)
    with connection:
        connection.execute(
            """
            INSERT INTO campaign_events (
                campaign_id, actor, event_type, details, recorded_at
            ) VALUES (?, ?, 'previewed', ?, ?)
            """,
            (campaign_id, actor, f"purpose={purpose.strip()}; count={len(recipients)}", timestamp),
        )
        connection.execute(
            """
            INSERT INTO subscriber_access_log (
                subscriber_id, admin_actor, action, purpose, accessed_at
            ) VALUES (NULL, ?, 'list_profiles', ?, ?)
            """,
            (actor, f"campaign:{campaign_id}:{purpose.strip()}", timestamp),
        )
    return CampaignPreview(campaign_id, len(recipients), by_channel, tuple(recipients))


def approve_campaign(
    connection: sqlite3.Connection,
    campaign_id: int,
    approved_by: str,
    at: datetime | None = None,
) -> None:
    actor = _require_actor(approved_by)
    timestamp = _timestamp(at)
    with connection:
        cursor = connection.execute(
            """
            UPDATE campaigns SET status = 'approved', approved_by = ?, approved_at = ?
            WHERE id = ? AND status = 'draft'
            """,
            (actor, timestamp, campaign_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("only a draft campaign can be approved")
        connection.execute(
            """
            INSERT INTO campaign_events (
                campaign_id, actor, event_type, details, recorded_at
            ) VALUES (?, ?, 'approved', NULL, ?)
            """,
            (campaign_id, actor, timestamp),
        )


def schedule_campaign(
    connection: sqlite3.Connection,
    campaign_id: int,
    scheduled_for: datetime,
    admin_actor: str,
    at: datetime | None = None,
) -> None:
    actor = _require_actor(admin_actor)
    now = at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    planned = scheduled_for
    if planned.tzinfo is None:
        planned = planned.replace(tzinfo=timezone.utc)
    if planned.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
        raise ValueError("campaign must be scheduled in the future")
    scheduled_at = _timestamp(planned)
    timestamp = _timestamp(now)
    with connection:
        cursor = connection.execute(
            """
            UPDATE campaigns SET status = 'scheduled', scheduled_at = ?
            WHERE id = ? AND status = 'approved'
            """,
            (scheduled_at, campaign_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("campaign must be approved before scheduling")
        connection.execute(
            """
            INSERT INTO campaign_events (
                campaign_id, actor, event_type, details, recorded_at
            ) VALUES (?, ?, 'scheduled', ?, ?)
            """,
            (campaign_id, actor, scheduled_at, timestamp),
        )
