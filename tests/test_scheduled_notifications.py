from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.notifications import THEATRE_TIMEZONE, pending_notifications
from theatre_bot.notification_jobs import run_service_job, planned_checks, fetch_month
from theatre_bot.outgoing_delivery import ChannelReplySender, VkApiSender, MemoryHttpTransport
from theatre_bot.persistent_replies import deliver_persistent_replies
from theatre_bot.persistent_delivery_worker import PersistentDeliveryWorker
from theatre_bot.subscribers import (connect_subscribers, initialize_subscribers, IdentityProtector,
    record_consent, subscribe_to_play, unsubscribe, configure_play_notifications)
from theatre_bot.update_lock import update_lock, UpdateAlreadyRunning


class ScheduledNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tp, self.sp = root / 'theatre.sqlite3', root / 'subscribers.sqlite3'
        self.theatre = connect(self.tp)
        initialize(self.theatre)
        self.subscribers = connect_subscribers(self.sp)
        initialize_subscribers(self.subscribers)
        self.protector = IdentityProtector(Fernet.generate_key(), b'j' * 32)
        self.start = datetime(2026, 10, 25, 18, tzinfo=THEATRE_TIMEZONE)
        self.prepare_time = self.start - timedelta(hours=26)
        self.item = AfficheItem('Гамлет', 'https://www.cheldrama.ru/plays/hamlet/',
            '2026-10-25T18:00', 'Драма', '16+', None, 'Большая сцена', None, 'event-1')
        self.other = replace(self.item, title='Другое', ticket_event_id='other', starts_at='2026-10-28T18:00')
        sync_affiche(self.theatre, [self.item, self.other], now=self.start-timedelta(days=7))
        self.add_subscriber('123')
        self.calls = []
        self.items = [self.item, self.other]
        self.transport = MemoryHttpTransport()
        self.sender = ChannelReplySender(vk_sender=VkApiSender('fake', self.transport))

    def tearDown(self):
        self.subscribers.close()
        self.theatre.close()
        self.tmp.cleanup()

    def add_subscriber(self, user):
        for kind in ('personal_data', 'service_notifications'):
            record_consent(self.subscribers, self.protector, 'vk', user, kind, 'v1', True,
                           'test', self.start-timedelta(days=7))
        subscribe_to_play(self.subscribers, self.protector, 'vk', user,
            self.item.play_url, self.item.title, self.start-timedelta(days=7),
            performance_key='kassy:event-1', selected_starts_at=self.item.starts_at)

    def fetch(self, year, month):
        self.calls.append((year, month))
        return self.items

    def run_job(self, now=None, check=False):
        return run_service_job(self.tp, self.sp, self.protector,
            now=now or self.prepare_time, check_only=check, fetcher=self.fetch)

    def deliver(self, now):
        return deliver_persistent_replies(self.subscribers, self.protector,
            self.sender, now=now, theatre_connection=self.theatre)

    def test_four_milestones_in_local_timezone(self):
        row = self.theatre.execute("SELECT * FROM performances WHERE source_key='kassy:event-1'").fetchone()
        plan = {c.stage: c.due_at for c in planned_checks(row)}
        self.assertEqual(plan['prepare'], self.prepare_time)
        self.assertEqual(plan['evening'], datetime(2026, 10, 24, 20, tzinfo=THEATRE_TIMEZONE))
        self.assertEqual(plan['morning'], datetime(2026, 10, 25, 9, tzinfo=THEATRE_TIMEZONE))
        self.assertEqual(plan['three_hours'], datetime(2026, 10, 25, 15, tzinfo=THEATRE_TIMEZONE))

    def test_no_due_check_means_no_site_request(self):
        result = self.run_job(self.start-timedelta(days=5))
        self.assertEqual((result.due_checks, result.queued, self.calls), (0, 0, []))

    def test_check_mode_has_no_network_and_no_database_changes(self):
        before = [p.read_bytes() for p in (self.tp, self.sp)]
        result = self.run_job(check=True)
        self.assertEqual((result.due_checks, result.fetched_months), (1, 1))
        self.assertEqual(self.calls, [])
        self.assertEqual([p.read_bytes() for p in (self.tp, self.sp)], before)

    def test_prepare_two_hours_early_but_deliver_at_twenty_four_hours(self):
        result = self.run_job()
        self.assertEqual((result.reminders, result.queued), (1, 1))
        self.assertEqual(self.calls, [(2026, 10)])
        self.assertEqual(len(pending_notifications(self.subscribers, self.protector, now=self.prepare_time)), 0)
        self.assertEqual(self.deliver(self.start-timedelta(hours=24, seconds=1)).sent, 0)
        self.assertEqual(self.deliver(self.start-timedelta(hours=24)).sent, 1)
        self.assertEqual(len(self.transport.requests), 1)

    def test_repeated_planner_ticks_do_not_fetch_or_queue_again(self):
        self.run_job()
        result = self.run_job(self.prepare_time+timedelta(minutes=15))
        self.assertEqual((result.queued, result.fetched_months), (0, 0))
        self.assertEqual(len(self.calls), 1)

    def test_evening_morning_three_hours_each_fetch_once(self):
        self.run_job()
        for at in [datetime(2026, 10, 24, 20, tzinfo=THEATRE_TIMEZONE),
                   datetime(2026, 10, 25, 9, tzinfo=THEATRE_TIMEZONE),
                   self.start-timedelta(hours=3)]:
            self.run_job(at)
            self.run_job(at+timedelta(minutes=15))
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.subscribers.execute('SELECT count(*) FROM notification_checks').fetchone()[0], 4)

    def test_one_month_request_for_multiple_subscribed_shows(self):
        subscribe_to_play(self.subscribers, self.protector, 'vk', '123', self.item.play_url,
            'Другое', self.prepare_time, performance_key='kassy:other', selected_starts_at=self.other.starts_at)
        with self.theatre:
            self.theatre.execute("UPDATE performances SET starts_at='2026-10-25T19:00' WHERE source_key='kassy:other'")
        self.items = [self.item, replace(self.other, starts_at='2026-10-25T19:00')]
        self.run_job(self.prepare_time+timedelta(hours=1))
        self.assertEqual(self.calls, [(2026, 10)])

    def test_new_subscriber_after_preparation_receives_own_reminder(self):
        self.run_job()
        self.add_subscriber('456')
        result = self.run_job(self.prepare_time+timedelta(minutes=15))
        self.assertEqual((result.reminders, result.queued, len(self.calls)), (1, 1, 1))
        self.assertEqual(self.deliver(self.start-timedelta(hours=24)).sent, 2)

    def test_unsubscribe_before_due_time_prevents_delivery(self):
        self.run_job()
        unsubscribe(self.subscribers, self.protector, 'vk', '123', 'v1', 'test')
        self.assertEqual(self.deliver(self.start-timedelta(hours=24)).sent, 0)
        self.assertEqual(len(self.transport.requests), 0)

    def test_reminder_preference_is_rechecked_before_sending(self):
        self.run_job()
        configure_play_notifications(self.subscribers, self.protector, 'vk', '123',
            'performance:kassy:event-1', True, False)
        self.assertEqual(self.deliver(self.start-timedelta(hours=24)).sent, 0)

    def test_removed_performance_cancels_prepared_reminder(self):
        self.run_job()
        with self.theatre:
            self.theatre.execute("UPDATE performances SET status='removed' WHERE source_key='kassy:event-1'")
        report = self.deliver(self.start-timedelta(hours=24))
        self.assertEqual((report.sent, report.cancelled), (0, 1))

    def test_rescheduled_performance_cancels_old_prepared_reminder(self):
        self.run_job()
        with self.theatre:
            self.theatre.execute("UPDATE performances SET starts_at='2026-10-26T18:00' WHERE source_key='kassy:event-1'")
        self.assertEqual(self.deliver(self.start-timedelta(hours=24)).cancelled, 1)

    def test_expired_reminder_after_long_outage_is_cancelled(self):
        self.run_job()
        self.assertEqual(self.deliver(self.start+timedelta(minutes=1)).cancelled, 1)

    def test_worker_uses_theatre_database_for_last_check(self):
        self.run_job()
        with self.theatre:
            self.theatre.execute("UPDATE performances SET status='removed' WHERE source_key='kassy:event-1'")
        worker = PersistentDeliveryWorker(self.sp, self.protector, self.sender,
                                         auto_start=False, theatre_database_path=self.tp)
        try:
            report = worker.run_once(now=self.start-timedelta(hours=24))
            self.assertEqual((report.sent, report.cancelled), (0, 1))
        finally:
            worker.close()

    def test_valid_month_missing_show_sends_removal_once(self):
        self.items = [self.other]
        first = self.run_job()
        second = self.run_job(self.prepare_time+timedelta(minutes=15))
        self.assertEqual((first.changes, first.reminders, second.changes), (1, 0, 0))
        self.assertEqual(self.deliver(self.prepare_time).sent, 1)
        self.assertEqual(len(self.transport.requests), 1)

    def test_empty_or_wrong_month_does_not_mark_show_removed(self):
        for items in [[], [replace(self.other, starts_at='2026-11-01T18:00')]]:
            self.items = items
            with self.assertRaises(ValueError):
                self.run_job()
            row = self.theatre.execute("SELECT status FROM performances WHERE source_key='kassy:event-1'").fetchone()
            self.assertEqual(row[0], 'scheduled')
            self.assertEqual(self.subscribers.execute('SELECT count(*) FROM notification_checks').fetchone()[0], 0)

    def test_site_timeout_does_not_create_messages_or_finish_check(self):
        def fail(*args):
            raise TimeoutError('private response')
        with self.assertRaises(TimeoutError):
            run_service_job(self.tp, self.sp, self.protector, now=self.prepare_time, fetcher=fail)
        self.assertEqual(self.subscribers.execute('SELECT count(*) FROM outgoing_reply_queue').fetchone()[0], 0)
        self.assertEqual(self.subscribers.execute('SELECT count(*) FROM notification_checks').fetchone()[0], 0)

    def test_only_subscribed_show_is_updated_by_partial_check(self):
        self.items = [self.item]
        self.run_job()
        row = self.theatre.execute("SELECT status FROM performances WHERE source_key='kassy:other'").fetchone()
        self.assertEqual(row[0], 'scheduled')

    def test_sync_lock_prevents_requests(self):
        with update_lock(self.tp.parent / 'sync.lock'):
            with self.assertRaises(UpdateAlreadyRunning):
                self.run_job()
        self.assertEqual(self.calls, [])

    def test_no_active_subscriptions_means_no_request(self):
        unsubscribe(self.subscribers, self.protector, 'vk', '123', 'v1', 'test')
        result = self.run_job()
        self.assertEqual((result.subscriptions, result.queued, self.calls), (0, 0, []))

    def test_html_failure_is_not_interpreted_as_removal(self):
        with patch('theatre_bot.notification_jobs.fetch_affiche_html', return_value='<html>error</html>'):
            with self.assertRaises(ValueError):
                fetch_month(2026, 10)

    def test_sent_reminder_is_not_queued_after_database_reopen(self):
        self.run_job()
        self.deliver(self.start-timedelta(hours=24))
        self.subscribers.close()
        self.subscribers = connect_subscribers(self.sp)
        self.assertEqual(self.run_job(self.start-timedelta(hours=24)).queued, 0)
        self.assertEqual(len(self.transport.requests), 1)

    def test_read_only_preflight_does_not_need_encryption_keys(self):
        result = run_service_job(self.tp, self.sp, now=self.prepare_time, check_only=True)
        self.assertEqual(result.due_checks, 1)

    def test_missing_database_is_not_created(self):
        missing = self.tp.parent / 'missing.sqlite3'
        with self.assertRaises(FileNotFoundError):
            run_service_job(missing, self.sp, self.protector)
        self.assertFalse(missing.exists())

    def test_transfer_seen_by_full_sync_is_reported_at_old_milestone(self):
        self.items = [replace(self.item, starts_at='2026-10-30T18:00'), self.other]
        sync_affiche(self.theatre, self.items, now=self.prepare_time)
        at = datetime(2026, 10, 24, 20, tzinfo=THEATRE_TIMEZONE)
        result = self.run_job(at)
        self.assertEqual(result.changes, 1)
        self.assertEqual(self.deliver(at).sent, 1)
        self.assertEqual(self.calls, [(2026, 10)])

    def test_transfer_during_targeted_check_reports_and_prepares_new_time_later(self):
        self.items = [replace(self.item, starts_at='2026-10-26T18:00'), self.other]
        result = self.run_job()
        self.assertEqual((result.changes, result.reminders), (1, 0))
        self.assertEqual(self.deliver(self.prepare_time).sent, 1)
        second = self.run_job(self.prepare_time+timedelta(days=1))
        self.assertEqual(second.reminders, 1)
        self.assertEqual(self.deliver(self.start).sent, 1)

    def test_only_changes_preference_does_not_prepare_reminder(self):
        configure_play_notifications(self.subscribers, self.protector, 'vk', '123',
            'performance:kassy:event-1', True, False)
        result = self.run_job()
        self.assertEqual((result.due_checks, result.reminders, self.calls), (0, 0, []))

    def test_only_reminders_preference_skips_evening_and_morning_site_checks(self):
        configure_play_notifications(self.subscribers, self.protector, 'vk', '123',
            'performance:kassy:event-1', False, True)
        self.run_job()
        self.run_job(datetime(2026, 10, 24, 20, tzinfo=THEATRE_TIMEZONE))
        self.run_job(datetime(2026, 10, 25, 9, tzinfo=THEATRE_TIMEZONE))
        self.assertEqual(len(self.calls), 1)

    def test_utc_scheduler_time_matches_local_milestone(self):
        from datetime import timezone
        result = self.run_job(self.prepare_time.astimezone(timezone.utc))
        self.assertEqual((result.due_checks, result.reminders), (1, 1))

    def test_recovery_after_reminder_window_does_not_create_late_reminder(self):
        result = self.run_job(self.start-timedelta(hours=20))
        self.assertEqual(result.reminders, 0)
        self.assertEqual(self.subscribers.execute(
            "SELECT count(*) FROM notification_queue WHERE notification_type='reminder_24h'").fetchone()[0], 0)

    def test_preparation_migration_preserves_existing_notification(self):
        from theatre_bot.notifications import build_service_notifications
        build_service_notifications(self.theatre, self.subscribers, now=self.start-timedelta(hours=24))
        before = self.subscribers.execute('SELECT message FROM notification_queue').fetchone()[0]
        with self.subscribers:
            self.subscribers.execute('ALTER TABLE notification_queue DROP COLUMN deliver_after')
            self.subscribers.execute('ALTER TABLE notification_queue DROP COLUMN planned_starts_at')
        initialize_subscribers(self.subscribers)
        row = self.subscribers.execute('SELECT message, deliver_after, planned_starts_at FROM notification_queue').fetchone()
        self.assertEqual(tuple(row), (before, None, None))

    def test_prepared_reminder_is_not_delivered_hours_late_after_outage(self):
        self.run_job()
        report = self.deliver(self.start-timedelta(hours=20))
        self.assertEqual((report.sent, report.cancelled), (0, 1))
        self.assertEqual(len(self.transport.requests), 0)

if __name__ == '__main__':
    unittest.main()
