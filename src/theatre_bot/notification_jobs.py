"""Scheduled checks of subscribed performances, separate from full catalogue sync."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from pathlib import Path
import sqlite3

from theatre_bot.database import _source_key
from theatre_bot.notifications import THEATRE_TIMEZONE, _local_datetime, _timestamp, build_service_notifications
from theatre_bot.service_delivery import queue_service_notifications
from theatre_bot.site_affiche import fetch_affiche_html, parse_affiche, BASE_URL
from theatre_bot.subscribers import initialize_subscribers
from theatre_bot.update_lock import update_lock


@dataclass(frozen=True)
class Check:
    performance_key: str
    starts_at: str
    stage: str
    due_at: datetime


@dataclass(frozen=True)
class JobReport:
    subscriptions: int
    due_checks: int
    fetched_months: int
    changes: int = 0
    reminders: int = 0
    queued: int = 0


def _open(path, read_only=False):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError('existing database required')
    db = sqlite3.connect(path.resolve().as_uri() + ('?mode=ro' if read_only else '?mode=rw'), uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def subscribed_performances(theatre, subscribers):
    rows = subscribers.execute("""
        SELECT sub.performance_key, MAX(sub.notify_changes) AS changes,
               MAX(sub.remind_24h) AS reminders, MIN(sub.selected_starts_at) AS selected_start
        FROM subscriptions sub JOIN subscribers s ON s.id = sub.subscriber_id
        WHERE sub.status='active' AND s.status='active' AND sub.performance_key IS NOT NULL
          AND (sub.notify_changes=1 OR sub.remind_24h=1)
          AND (SELECT action FROM consent_events c WHERE c.subscriber_id=s.id
               AND c.consent_type='service_notifications' ORDER BY c.id DESC LIMIT 1)='granted'
        GROUP BY sub.performance_key
    """).fetchall()
    result = []
    for row in rows:
        performance = theatre.execute("""
            SELECT p.source_key, p.starts_at, p.status, pl.source_url AS play_key,
                   pl.title AS play_title
            FROM performances p JOIN plays pl ON pl.id=p.play_id WHERE p.source_key=?
        """, (row['performance_key'],)).fetchone()
        if performance is not None:
            value = dict(performance)
            observation = subscribers.execute(
                'SELECT starts_at FROM performance_observations WHERE source_key=?',
                (performance['source_key'],)).fetchone()
            value['observed_starts_at'] = observation[0] if observation else row['selected_start']
            value['changes'] = bool(row['changes'])
            value['reminders'] = bool(row['reminders'])
            result.append(value)
    return result


def planned_checks(performance):
    start = _local_datetime(performance['starts_at'])
    evening = datetime.combine(start.date() - timedelta(days=1), time(20), tzinfo=THEATRE_TIMEZONE)
    morning = datetime.combine(start.date(), time(9), tzinfo=THEATRE_TIMEZONE)
    for stage, due in [('evening', evening), ('morning', morning),
                       ('three_hours', start - timedelta(hours=3)),
                       ('prepare', start - timedelta(hours=26))]:
        if due < start:
            yield Check(performance['source_key'], performance['starts_at'], stage, due)


def _due_checks(subscribers, performances, now):
    has_table = subscribers.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_checks'").fetchone()
    result = []
    for performance in performances:
        actual_start = _local_datetime(performance['starts_at'])
        old = performance.get('observed_starts_at')
        versions = {performance['starts_at']}
        if old:
            versions.add(old)
        for version in sorted(versions):
            scheduled = dict(performance, starts_at=version)
            start = _local_datetime(version)
            deadline_end = max(start, actual_start)
            if deadline_end <= now:
                continue
            for check in planned_checks(scheduled):
                if check.stage == 'prepare':
                    if not performance['reminders'] or version != performance['starts_at']:
                        continue
                    deadline = start - timedelta(hours=23, minutes=30)
                else:
                    if not performance['changes']:
                        continue
                    deadline = deadline_end
                if not check.due_at <= now < deadline:
                    continue
                done = has_table and subscribers.execute(
                    'SELECT 1 FROM notification_checks WHERE performance_key=? AND starts_at=? AND stage=?',
                    (check.performance_key, check.starts_at, check.stage),
                ).fetchone()
                if not done:
                    result.append(check)
    return result


def fetch_month(year, month):
    html = fetch_affiche_html(f'{BASE_URL}/affiche/{year:04d}/{month:02d}/')
    from datetime import date
    items = [item for item in parse_affiche(html, today=date(year, month, 1))
             if datetime.fromisoformat(item.starts_at).strftime('%Y-%m') == f'{year:04d}-{month:02d}']
    # An empty/unrecognisable page is an error, not evidence of cancellation.
    if not items:
        raise ValueError('monthly affiche is empty or unrecognisable')
    return items


def _refresh(theatre, performances, due, fetcher, now):
    keys = {check.performance_key for check in due}
    targets = [p for p in performances if p['source_key'] in keys]
    months = sorted({_local_datetime(p['starts_at']).strftime('%Y-%m') for p in targets})
    items = []
    # Collect and validate every response before changing any performance.
    for month in months:
        year, number = map(int, month.split('-'))
        found = fetcher(year, number)
        if not found or any(datetime.fromisoformat(item.starts_at).strftime('%Y-%m') != month for item in found):
            raise ValueError('monthly affiche response does not match requested month')
        items.extend(found)
    by_key = {_source_key(item): item for item in items}
    with theatre:
        for performance in targets:
            item = by_key.get(performance['source_key'])
            if item is None:
                theatre.execute("UPDATE performances SET status='removed', synced_at=? WHERE source_key=?",
                                (_timestamp(now), performance['source_key']))
            else:
                theatre.execute("""UPDATE performances SET starts_at=?, venue=?, status='scheduled',
                                   ticket_event_id=?, synced_at=? WHERE source_key=?""",
                                (item.starts_at, item.venue, item.ticket_event_id,
                                 _timestamp(now), performance['source_key']))
    return len(months)


def run_service_job(theatre_path, subscriber_path, protector=None, now=None, check_only=False,
                    fetcher=fetch_month):
    now = now or datetime.now(THEATRE_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=THEATRE_TIMEZONE)
    now = now.astimezone(THEATRE_TIMEZONE)
    if not check_only and protector is None:
        raise ValueError('identity protector required')
    with closing(_open(theatre_path, read_only=check_only)) as theatre:
        with closing(_open(subscriber_path, read_only=check_only)) as subscribers:
            if check_only:
                performances = subscribed_performances(theatre, subscribers)
                due = _due_checks(subscribers, performances, now)
                months = {_local_datetime(c.starts_at).strftime('%Y-%m') for c in due}
                return JobReport(len(performances), len(due), len(months))
            with update_lock(Path(theatre_path).parent / 'sync.lock'):
                initialize_subscribers(subscribers)
                performances = subscribed_performances(theatre, subscribers)
                due = _due_checks(subscribers, performances, now)
                # Save the pre-refresh baseline only for newly observed shows.
                with subscribers:
                    for p in performances:
                        subscribers.execute("""INSERT OR IGNORE INTO performance_observations
                            (source_key, play_key, play_title, starts_at, status, observed_at)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                            (p['source_key'], p['play_key'], p['play_title'],
                             p.get('observed_starts_at') or p['starts_at'], 'scheduled', _timestamp(now)))
                fetched = _refresh(theatre, performances, due, fetcher, now)
                changed_keys = {c.performance_key for c in due}
                changed = build_service_notifications(theatre, subscribers, now=now,
                    performance_keys=changed_keys, include_reminders=False)
                # Re-read after refresh: a reschedule changes all time-based milestones.
                current = subscribed_performances(theatre, subscribers)
                confirmed = set()
                for p in current:
                    previous = subscribers.execute("""SELECT 1 FROM notification_checks
                        WHERE performance_key=? AND starts_at=? AND stage='prepare'""",
                        (p['source_key'], p['starts_at'])).fetchone()
                    just_checked = any(c.performance_key == p['source_key'] and c.starts_at == p['starts_at'] and c.stage == 'prepare' for c in due)
                    if previous or just_checked:
                        confirmed.add(p['source_key'])
                reminders = build_service_notifications(theatre, subscribers, now=now,
                    performance_keys=confirmed, include_changes=False, prepare_reminders=True)
                # Incoming rows have stable dedupe keys, so retry after a crash is safe.
                queued = queue_service_notifications(subscribers, protector, limit=1000,
                                                     now=now, include_future=True)
                with subscribers:
                    for c in due:
                        subscribers.execute('INSERT OR IGNORE INTO notification_checks VALUES (?, ?, ?, ?)',
                            (c.performance_key, c.starts_at, c.stage, _timestamp(now)))
                return JobReport(len(current), len(due), fetched,
                                 changed.rescheduled + changed.removed, reminders.reminders, queued)
