from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from theatre_bot.database import connect, initialize, sync_affiche
from theatre_bot.site_affiche import AfficheItem
from theatre_bot.subscribers import (connect_subscribers, initialize_subscribers, IdentityProtector,
    record_consent, subscribe_to_play, subscriber_profile)
from theatre_bot.channel_handler import ChannelMessageHandler
from theatre_bot.channels import IncomingMessage
from theatre_bot.dialog import Reply
from theatre_bot.spam_protection import check_message
from theatre_bot.interest_preferences import save_preferences, genre_options, season_year
from theatre_bot.interest_survey import InterestSurvey
from theatre_bot.campaigns import create_campaign, preview_campaign, approve_campaign, schedule_campaign
from theatre_bot.campaign_delivery import queue_due_campaigns
from theatre_bot.persistent_replies import deliver_persistent_replies
from theatre_bot.outgoing_delivery import ChannelReplySender, VkApiSender, MemoryHttpTransport


class InterestSurveyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.theatre = connect(self.root/'theatre.sqlite3')
        self.addCleanup(self.theatre.close)
        initialize(self.theatre)
        self.db = connect_subscribers(self.root/'subscribers.sqlite3')
        self.addCleanup(lambda: self.db.close())
        initialize_subscribers(self.db)
        self.protector = IdentityProtector(Fernet.generate_key(), b'q'*32)
        self.now = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)
        self.counter = 0
        self.channel, self.user = 'vk', '123'
        sync_affiche(self.theatre, [AfficheItem('Гамлет','https://www.cheldrama.ru/plays/hamlet/',
            '2026-11-20T18:00','Драма','16+',None,'Большая сцена',None,'one')], now=self.now)
        with self.theatre:
            self.theatre.execute("INSERT INTO artists(source_url, full_name, normalized_name, synced_at) VALUES('artist-1','Анна Иванова','анна иванова','now')")
            self.theatre.execute("INSERT INTO artists(source_url, full_name, normalized_name, synced_at) VALUES('artist-2','Пётр Петров','петр петров','now')")
        self.handler = self.make_handler()

    def make_handler(self):
        return ChannelMessageHandler(self.theatre, self.db, self.protector, survey_enabled=True)

    def send(self, text='', action=None):
        self.counter += 1
        return self.handler.process(IncomingMessage(self.channel, self.user, f'{self.user}-{self.channel}-{self.counter}', text, 'Зритель', action), now=self.now)

    def click(self, reply, label):
        button = next(v for v in reply.buttons if v.label==label)
        return self.send(action=button.action)

    def genres(self):
        intro = self.send('Анкета интересов')
        consent = self.click(intro, 'Начать')
        if any(b.label=='Согласен' for b in consent.buttons):
            return self.click(self.click(consent, 'Согласен'), 'Жанры')
        return self.click(consent, 'Жанры')

    def finish(self, marketing=True, premiere=True, genres=True, artist=True):
        reply = self.genres()
        if genres:
            reply = self.click(reply, 'Драма')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Артисты')
        if artist:
            reply = self.click(reply, 'Анна Иванова')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Премьеры')
        reply = self.click(reply, 'Да' if premiere else 'Нет')
        return self.click(self.click(reply, 'Завершить анкету'), 'Сохранить' if marketing else 'Не сохранять')

    def count(self, table):
        return self.db.execute('SELECT count(*) FROM '+table).fetchone()[0]

    def test_survey_play_interest_saved_for_season_and_can_be_cleared(self):
        intro=self.send('Анкета интересов')
        consent=self.click(intro,'Начать')
        menu=self.click(consent,'Согласен')
        choices=self.click(menu,'Спектакли')
        self.assertIn('2026/2027',choices.reply.text)
        choices=self.click(choices,'Гамлет')
        menu=self.click(choices,'К разделам анкеты')
        self.click(self.click(menu,'Завершить анкету'),'Сохранить')
        self.assertEqual(self.count('survey_play_interests'),1)
        self.assertEqual(self.count('subscriptions'),0)
        menu=self.click(self.send('Анкета интересов'),'Начать')
        choices=self.click(menu,'Спектакли')
        menu=self.click(self.click(choices,'Снять все отметки'),'К разделам анкеты')
        self.click(self.click(menu,'Завершить анкету'),'Сохранить')
        self.assertEqual(self.count('survey_play_interests'),0)

    def test_season_switches_after_july_tenth_in_theatre_time(self):
        self.assertEqual(season_year(datetime(2027,7,10,18,59,tzinfo=timezone.utc)),2026)
        self.assertEqual(season_year(datetime(2027,7,10,19,0,tzinfo=timezone.utc)),2027)

    def test_first_question_retains_answer_and_adds_brief_invitation_once(self):
        first = self.send('Здравствуйте')
        self.assertTrue(first.reply.text)
        self.assertIn('Анкета интересов', [b.label for b in first.buttons])
        self.assertNotIn('персональных', first.reply.text)
        self.assertEqual(self.count('subscribers'), 0)
        again = self.send('Здравствуйте')
        self.assertNotIn('Пропустить анкету', [b.label for b in again.buttons])

    def test_manual_menu_does_not_create_draft_after_restart(self):
        self.send('Здравствуйте')
        self.handler = self.make_handler()
        self.send('Здравствуйте')
        self.assertEqual(self.count('interest_survey_state'), 0)
        self.assertEqual(self.count('subscribers'), 0)
        self.assertIn('Начать', [b.label for b in self.send('Анкета интересов').buttons])

    def test_description_and_consent_precede_answers(self):
        intro = self.send('Анкета интересов')
        self.assertIn('добровольная', intro.reply.text)
        consent = self.click(intro, 'Начать')
        self.assertIn('персональных', consent.reply.text)
        self.assertEqual(self.count('subscribers'), 0)
        self.assertEqual(self.count('preferences'), 0)

    def test_legal_document_screen_returns_to_consent(self):
        intro = self.send('Анкета интересов')
        consent = self.click(intro, 'Начать')
        legal = self.click(consent, 'Открыть документы')
        self.assertIn('/legal/privacy', legal.reply.text)
        self.assertIn('Согласен', [b.label for b in self.click(legal, 'Назад').buttons])

    def test_interests_save_with_advertising_permission_without_service_subscription(self):
        result = self.finish()
        self.assertIn('Интересы сохранены', result.reply.text)
        self.assertEqual(self.count('subscriptions'), 0)
        profile = subscriber_profile(self.db, self.protector, 1, 'admin', 'test')
        self.assertTrue(profile.interested_in_premieres)
        self.assertTrue(profile.consents['marketing'])
        self.assertNotIn('service_notifications', dict(self.db.execute('SELECT consent_type, action FROM consent_events').fetchall()))
        self.assertIn(('genre','Драма'), profile.preferences)
        self.assertIn(('artist','Анна Иванова'), profile.preferences)
        self.assertEqual(profile.external_id, '123')

    def test_genre_artist_and_premiere_filter_selects_only_marketing_opt_in(self):
        self.finish(marketing=True)
        campaign = create_campaign(self.db, 'Премьера', 'Приглашаем', 'admin',
            target_type='genre', target_key='драма', premieres_only=True, at=self.now)
        preview = preview_campaign(self.db, self.protector, campaign, 'admin', 'test', at=self.now)
        self.assertEqual(preview.total, 1)
        artist_campaign = create_campaign(self.db, 'Артист', 'Приглашаем', 'admin',
            target_type='artist', target_key='artist-1', at=self.now)
        self.assertEqual(preview_campaign(self.db, self.protector, artist_campaign, 'admin', 'test', at=self.now).total, 1)

    def test_declined_marketing_excludes_saved_interests_from_campaign(self):
        self.finish(marketing=False)
        campaign = create_campaign(self.db, 'Премьера', 'Приглашаем', 'admin', premieres_only=True, at=self.now)
        self.assertEqual(preview_campaign(self.db, self.protector, campaign, 'admin', 'test', at=self.now).total, 0)

    def test_premiere_filter_excludes_other_interests(self):
        self.finish(marketing=True, premiere=False)
        campaign = create_campaign(self.db, 'Премьера', 'Приглашаем', 'admin', premieres_only=True, at=self.now)
        self.assertEqual(preview_campaign(self.db, self.protector, campaign, 'admin', 'test', at=self.now).total, 0)

    def test_real_delivery_path_works_with_survey_only_profile_using_fake_transport(self):
        self.finish(marketing=True)
        campaign = create_campaign(self.db, 'Премьера', 'Приглашаем', 'admin', premieres_only=True, at=self.now)
        approve_campaign(self.db, campaign, 'admin', at=self.now)
        due = self.now+timedelta(hours=1)
        schedule_campaign(self.db, campaign, due, 'admin', at=self.now)
        self.assertEqual(queue_due_campaigns(self.db, self.protector, now=due), 1)
        transport = MemoryHttpTransport()
        sender = ChannelReplySender(vk_sender=VkApiSender('fake', transport))
        self.assertEqual(deliver_persistent_replies(self.db, self.protector, sender, now=due).sent, 1)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(self.count('subscriptions'), 0)

    def test_no_preference_saved_before_final_advertising_choice(self):
        genres = self.genres()
        self.click(genres, 'Драма')
        self.assertEqual(self.count('preferences'), 0)
        state = self.db.execute('SELECT payload_encrypted FROM interest_survey_state').fetchone()[0]
        self.assertNotIn('Драма'.encode(), bytes(state))

    def test_cancel_edit_preserves_existing_preferences_and_consent(self):
        self.finish(marketing=True)
        before = [tuple(r) for r in self.db.execute('SELECT * FROM preferences')]
        self.genres()
        self.send('Отмена')
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM preferences')], before)
        self.assertEqual(self.db.execute("SELECT action FROM consent_events WHERE consent_type='marketing' ORDER BY id DESC LIMIT 1").fetchone()[0], 'granted')

    def test_saved_edit_can_clear_choices_without_touching_play_interest(self):
        self.finish(marketing=True)
        with self.db:
            self.db.execute("INSERT INTO preferences(subscriber_id,preference_type,preference_key,preference_label,source,recorded_at) VALUES(1,'play','play','Гамлет','subscription','now')")
        reply = self.genres()
        reply = self.click(reply, '✓ Драма')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Артисты')
        reply = self.click(reply, '✓ Анна Иванова')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Премьеры')
        reply = self.click(self.click(reply, 'Нет'), 'Завершить анкету')
        self.click(reply, 'Сохранить')
        rows = [tuple(r) for r in self.db.execute('SELECT preference_type,preference_label FROM preferences')]
        self.assertEqual(rows, [('play','Гамлет')])
        self.assertEqual(self.db.execute('SELECT interested_in_premieres FROM subscribers').fetchone()[0], 0)

    def test_old_toggle_cannot_be_replayed(self):
        reply = self.genres()
        old = next(b.action for b in reply.buttons if b.label=='Драма')
        self.send(action=old)
        result = self.send(action=old)
        self.assertIn('неактуальна', result.reply.text)
        self.assertEqual(self.count('preferences'), 0)

    def test_cross_user_and_cross_channel_buttons_cannot_change_draft(self):
        genres = self.genres()
        action = next(b.action for b in genres.buttons if b.label=='Драма')
        self.user = 'other'
        self.assertIn('неактуальна', self.send(action=action).reply.text)
        self.user, self.channel = '123','max'
        self.assertIn('неактуальна', self.send(action=action).reply.text)

    def test_encrypted_draft_survives_server_restart(self):
        genres = self.genres()
        updated = self.click(genres, 'Драма')
        self.db.close()
        self.db = connect_subscribers(self.root/'subscribers.sqlite3')
        initialize_subscribers(self.db)
        self.handler = self.make_handler()
        artists = self.click(self.click(updated, 'К разделам анкеты'), 'Артисты')
        self.assertIn('артистов', artists.reply.text)

    def test_expired_draft_cannot_accept_old_button(self):
        genres = self.genres()
        self.now += timedelta(minutes=31)
        reply = self.click(genres, 'Драма')
        self.assertIn('неактуальна', reply.reply.text)
        self.assertEqual(self.db.execute('SELECT payload_encrypted FROM interest_survey_state').fetchone()[0], None)

    def test_artist_name_search_and_empty_search_can_be_skipped(self):
        genres = self.genres()
        self.click(self.click(genres, 'К разделам анкеты'), 'Артисты')
        result = self.send('Иванова')
        self.assertIn('Анна Иванова', [b.label for b in result.buttons])
        self.assertNotIn('Пётр Петров', [b.label for b in result.buttons])
        result = self.send('Несуществующая фамилия')
        self.assertIn('Подходящих вариантов нет', result.reply.text)
        self.assertIn('Анкета интересов', self.click(result, 'К разделам анкеты').reply.text)

    def test_ordinary_question_can_exit_non_artist_step(self):
        self.genres()
        reply = self.send('Гамлет')
        self.assertTrue(reply.reply.cards)
        self.assertEqual(self.db.execute('SELECT stage FROM interest_survey_state').fetchone()[0], 'closed')

    def test_unsubscribe_invalidates_unfinished_questionnaire(self):
        genres = self.genres()
        self.send('Отписаться')
        self.assertIn('неактуальна', self.click(genres, 'Драма').reply.text)

    def test_cancel_remains_available_during_spam_pause(self):
        self.genres()
        for _ in range(5):
            check_message(self.db,self.protector,'vk','123','repeat',now=self.now)
        result = self.send('Отмена')
        self.assertIn('Новые ответы не сохранены', result.reply.text)

    def test_save_requires_personal_data_consent(self):
        with self.assertRaises(PermissionError):
            save_preferences(self.db,self.theatre,self.protector,'vk','123',None,['драма'],[],True,True,'v1',now=self.now)
        self.assertEqual(self.count('subscribers'), 0)

    def test_removed_artist_is_not_saved_from_stale_catalogue(self):
        genres = self.genres()
        artists = self.click(self.click(genres, 'К разделам анкеты'), 'Артисты')
        artists = self.click(artists, 'Анна Иванова')
        premiere = self.click(self.click(artists, 'К разделам анкеты'), 'Премьеры')
        marketing = self.click(self.click(premiere, 'Да'), 'Завершить анкету')
        with self.theatre:
            self.theatre.execute("UPDATE artists SET is_active=0 WHERE source_url='artist-1'")
        result = self.click(marketing, 'Сохранить')
        self.assertIn('изменился', result.reply.text)
        self.assertEqual(self.count('preferences'), 0)

    def test_migration_keeps_existing_data_and_is_repeatable(self):
        self.finish()
        with self.db:
            self.db.execute('ALTER TABLE subscribers DROP COLUMN interested_in_premieres')
            self.db.execute('ALTER TABLE campaigns DROP COLUMN premieres_only')
        initialize_subscribers(self.db)
        initialize_subscribers(self.db)
        self.assertEqual(self.count('preferences'), 2)
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_max_uses_the_same_questionnaire(self):
        self.channel = 'max'
        result = self.finish()
        self.assertIn('Интересы сохранены', result.reply.text)
        self.assertEqual(self.db.execute('SELECT channel FROM subscribers').fetchone()[0], 'max')


    def test_artist_pagination_retains_selections_and_limits_button_count(self):
        with self.theatre:
            for number in range(3, 8):
                self.theatre.execute("INSERT INTO artists(source_url,full_name,normalized_name,synced_at) VALUES(?,?,?,'now')",
                    (f'artist-{number}', f'Артист {number}', f'артист {number}'))
        reply = self.click(self.click(self.genres(), 'К разделам анкеты'), 'Артисты')
        chosen = reply.buttons[0].label
        reply = self.click(reply, chosen)
        for _ in range(3):
            self.assertLessEqual(len(reply.buttons), 6)
            reply = self.click(reply, 'Ещё артисты')
        self.assertIn(chosen, reply.reply.text)

    def test_fairy_tale_is_a_genre_from_active_theatre_data(self):
        with self.theatre:
            self.theatre.execute("UPDATE plays SET genre='Сказка'")
        self.assertIn({'key':'сказка','label':'Сказка'}, genre_options(self.theatre))

    def test_artist_search_accepts_e_instead_of_yo(self):
        self.click(self.click(self.genres(), 'К разделам анкеты'), 'Артисты')
        reply = self.send('Петр')
        self.assertIn('Пётр Петров', [b.label for b in reply.buttons])

    def test_survey_does_not_change_existing_performance_subscription(self):
        for kind in ('personal_data', 'service_notifications'):
            record_consent(self.db,self.protector,'vk','123',kind,'v1',True,'test',at=self.now)
        subscribe_to_play(self.db,self.protector,'vk','123','https://www.cheldrama.ru/plays/hamlet/',
            'Гамлет',self.now,performance_key='kassy:one',selected_starts_at='2026-11-20T18:00')
        before = [tuple(row) for row in self.db.execute('SELECT * FROM subscriptions')]
        self.finish()
        self.assertEqual([tuple(row) for row in self.db.execute('SELECT * FROM subscriptions')], before)
        self.assertEqual(self.db.execute("SELECT action FROM consent_events WHERE consent_type='service_notifications' ORDER BY id DESC LIMIT 1").fetchone()[0], 'granted')

    def test_revoked_personal_consent_requires_confirmation_again(self):
        reply = self.genres()
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Артисты')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Премьеры')
        reply = self.click(self.click(reply, 'Да'), 'Завершить анкету')
        record_consent(self.db,self.protector,'vk','123','personal_data','v1',False,'test',at=self.now)
        result = self.click(reply, 'Сохранить')
        self.assertIn('Согласен', [b.label for b in result.buttons])
        self.assertEqual(self.count('preferences'), 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM consent_events WHERE consent_type='marketing'").fetchone()[0], 0)

    def test_completed_questionnaire_cannot_regrant_ads_after_revocation(self):
        reply = self.genres()
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Артисты')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Премьеры')
        reply = self.click(self.click(reply, 'Да'), 'Завершить анкету')
        old_action = next(b.action for b in reply.buttons if b.label=='Сохранить')
        self.send(action=old_action)
        record_consent(self.db,self.protector,'vk','123','marketing','v1',False,'test',at=self.now)
        result = self.send(action=old_action)
        self.assertIn('неактуальна', result.reply.text)
        self.assertEqual(self.db.execute("SELECT action FROM consent_events WHERE consent_type='marketing' ORDER BY id DESC LIMIT 1").fetchone()[0], 'revoked')

    def test_save_failure_rolls_back_preferences_consent_and_draft_completion(self):
        import sqlite3
        genres = self.genres()
        artists = self.click(genres, 'Драма')
        artists = self.click(self.click(artists, 'К разделам анкеты'), 'Артисты')
        premiere = self.click(self.click(artists, 'К разделам анкеты'), 'Премьеры')
        self.click(self.click(premiere, 'Да'), 'Завершить анкету')
        survey = InterestSurvey(self.theatre,self.db,self.protector)
        message = IncomingMessage('vk','123','test','')
        _, data = survey._load(message, self.now)
        self.db.execute("CREATE TRIGGER fail_survey_save BEFORE INSERT ON consent_events WHEN NEW.consent_type='marketing' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            save_preferences(self.db,self.theatre,self.protector,'vk','123',None,['драма'],[],True,True,'v1',now=self.now,
                             survey_actor=survey._actor(message),survey_token=data['token'])
        self.assertEqual(self.count('preferences'), 0)
        self.assertEqual(self.db.execute('SELECT interested_in_premieres FROM subscribers').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT stage FROM interest_survey_state').fetchone()[0], 'marketing')

    def test_not_saving_discards_answers_and_delivery_identity_for_unused_profile(self):
        result = self.finish(marketing=False)
        self.assertIn('Новые ответы не сохранены', result.reply.text)
        self.assertEqual(self.count('preferences'), 0)
        row = self.db.execute('SELECT status, interested_in_premieres, external_id_encrypted, display_name_encrypted FROM subscribers').fetchone()
        self.assertEqual(tuple(row), ('pending', 0, None, None))
        self.assertEqual(self.db.execute("SELECT count(*) FROM consent_events WHERE consent_type='marketing' AND action='granted'").fetchone()[0], 0)
        self.assertIsNone(self.db.execute('SELECT payload_encrypted FROM interest_survey_state').fetchone()[0])

    def test_no_invitation_after_calendar_month(self):
        self.click(self.send('Анкета интересов'), 'Пропустить')
        self.now += timedelta(days=65)
        result = self.send('Здравствуйте')
        self.assertNotIn('Хотите настроить интересы?', result.reply.text)
        self.assertNotIn('Пропустить анкету', [b.label for b in result.buttons])

    def test_repeated_cancellation_never_offers_reminders(self):
        for _ in range(3):
            result = self.click(self.send('Анкета интересов'), 'Пропустить')
            self.assertNotIn('месяц', result.reply.text)
            self.assertEqual([b.label for b in result.buttons], ['Анкета интересов','Обратная связь'])

    def test_legacy_due_reminder_is_ignored(self):
        self.click(self.send('Анкета интересов'), 'Пропустить')
        with self.db:
            self.db.execute("UPDATE interest_survey_state SET next_offer_at='2020-01-01T00:00:00+00:00'")
        self.assertNotIn('Хотите настроить интересы?', self.send('Здравствуйте').reply.text)

    def test_repeated_decline_button_counts_once(self):
        intro = self.send('Анкета интересов')
        action = next(b.action for b in intro.buttons if b.label=='Пропустить')
        self.send(action=action)
        self.send(action=action)
        self.assertEqual(self.db.execute('SELECT decline_count FROM interest_survey_state').fetchone()[0], 0)

    def test_calendar_month_handles_year_and_short_month(self):
        from theatre_bot.interest_survey import next_month
        self.assertEqual(next_month(datetime(2026,1,31,tzinfo=timezone.utc)), datetime(2026,2,28,tzinfo=timezone.utc))
        self.assertEqual(next_month(datetime(2026,12,31,tzinfo=timezone.utc)), datetime(2027,1,31,tzinfo=timezone.utc))

    def test_not_saving_edit_preserves_old_interests_and_advertising_permission(self):
        self.finish(marketing=True)
        before = [tuple(r) for r in self.db.execute('SELECT * FROM preferences')]
        self.now += timedelta(minutes=2)
        reply = self.genres()
        reply = self.click(reply, '✓ Драма')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Артисты')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Премьеры')
        reply = self.click(self.click(reply, 'Нет'), 'Завершить анкету')
        self.click(reply, 'Не сохранять')
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM preferences')], before)
        self.assertEqual(self.db.execute('SELECT interested_in_premieres FROM subscribers').fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT action FROM consent_events WHERE consent_type='marketing' ORDER BY id DESC LIMIT 1").fetchone()[0], 'granted')

    def test_vk_menu_keyboard_survives_encrypted_queue_roundtrip(self):
        import json
        from urllib.parse import parse_qs
        from theatre_bot.persistent_replies import enqueue_reply
        reply = self.click(self.send('Анкета интересов'), 'Пропустить')
        self.assertFalse(reply.keyboard_inline)
        enqueue_reply(self.db, self.protector, reply, 'menu', at=self.now)
        transport = MemoryHttpTransport()
        sender = ChannelReplySender(vk_sender=VkApiSender('fake', transport))
        deliver_persistent_replies(self.db, self.protector, sender, now=self.now)
        fields = parse_qs(transport.requests[0].body.decode())
        keyboard = json.loads(fields['keyboard'][0])
        self.assertFalse(keyboard['inline'])
        self.assertFalse(keyboard['one_time'])
        self.assertEqual(keyboard['buttons'][0][0]['action']['label'], 'Анкета интересов')

    def test_completed_survey_is_not_offered_monthly(self):
        self.finish()
        self.now += timedelta(days=65)
        self.assertNotIn('Хотите настроить интересы?', self.send('Здравствуйте').reply.text)
        self.assertEqual(self.db.execute('SELECT decline_count FROM interest_survey_state').fetchone()[0], 0)

    def test_section_menu_allows_any_order_and_edit_before_saving(self):
        genres = self.genres()
        menu = self.click(genres, 'К разделам анкеты')
        self.assertEqual([b.label for b in menu.buttons], ['Жанры','Спектакли','Артисты','Премьеры','Завершить анкету','Удалить данные анкеты'])
        menu = self.click(self.click(menu, 'Премьеры'), 'Да')
        artists = self.click(menu, 'Артисты')
        artists = self.click(artists, 'Анна Иванова')
        menu = self.click(artists, 'К разделам анкеты')
        genres = self.click(menu, 'Жанры')
        genres = self.click(genres, 'Драма')
        menu = self.click(genres, 'К разделам анкеты')
        final = self.click(menu, 'Завершить анкету')
        menu = self.click(final, 'Изменить ответы')
        self.assertIn('Анна Иванова', menu.reply.text)
        self.assertIn('Драма', menu.reply.text)
        self.assertIn('Премьеры: да', menu.reply.text)
        final = self.click(menu, 'Завершить анкету')
        self.assertIn('Интересы сохранены', self.click(final, 'Сохранить').reply.text)

    def test_author_subtitles_are_not_questionnaire_categories(self):
        with self.theatre:
            self.theatre.execute("UPDATE plays SET genre='Встреча сообщества Подтекст'")
        labels = [v['label'] for v in genre_options(self.theatre)]
        self.assertEqual(labels, ['Комедия','Драма','Трагедия','Мелодрама','Музыкальный спектакль','Сказка'])

    def test_date_and_subscription_buttons_never_include_survey_invitation(self):
        from theatre_bot.channels import OutgoingButton, OutgoingMessage
        survey = InterestSurvey(self.theatre, self.db, self.protector)
        incoming = IncomingMessage('vk','123','test','Гамлет')
        outgoing = OutgoingMessage('vk','123', Reply('Выберите дату'),
                                   (OutgoingButton('choose:test','20 ноября'),))
        result = survey.offer(incoming, outgoing, self.now)
        self.assertEqual(result, outgoing)
        self.assertEqual(self.count('interest_survey_state'), 0)

    def test_first_message_has_only_manual_vk_keyboard(self):
        reply = self.send('Здравствуйте')
        self.assertEqual([b.label for b in reply.buttons], ['Анкета интересов','Обратная связь'])
        self.assertFalse(reply.keyboard_inline)
        self.assertNotIn('Хотите настроить интересы?', reply.reply.text)
        self.assertEqual(self.count('interest_survey_state'), 0)


    def test_delete_interests_revokes_marketing_preserves_service(self):
        self.finish()
        record_consent(self.db, self.protector, self.channel, self.user, 'service_notifications', 'v1', True, 'test', at=self.now)
        subscribe_to_play(self.db, self.protector, self.channel, self.user, 'play', 'Спектакль', at=self.now)
        before = [tuple(r) for r in self.db.execute('SELECT * FROM subscriptions')]
        confirmation = self.click(self.send('Анкета интересов'), 'Удалить данные анкеты')
        self.assertGreater(self.count('preferences'), 0)
        result = self.click(confirmation, 'Да, удалить')
        self.assertIn('Данные анкеты удалены', result.reply.text)
        profile = subscriber_profile(self.db, self.protector, 1, 'admin', 'test')
        self.assertFalse(profile.consents['marketing'])
        self.assertTrue(profile.consents['service_notifications'])
        self.assertTrue(profile.consents['personal_data'])
        self.assertFalse(profile.interested_in_premieres)
        self.assertFalse(any(kind in {'genre','artist'} for kind,_ in profile.preferences))
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM subscriptions')], before)
        self.assertIsNone(self.db.execute('SELECT payload_encrypted FROM interest_survey_state').fetchone()[0])

    def test_delete_cancel_and_expired_button_preserve_interests(self):
        self.finish()
        before = [tuple(r) for r in self.db.execute('SELECT * FROM preferences')]
        confirmation = self.click(self.send('Анкета интересов'), 'Удалить данные анкеты')
        self.click(confirmation, 'Не удалять')
        self.click(confirmation, 'Да, удалить')
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM preferences')], before)
        confirmation = self.click(self.send('Анкета интересов'), 'Удалить данные анкеты')
        self.now += timedelta(minutes=31)
        self.click(confirmation, 'Да, удалить')
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM preferences')], before)

    def test_selected_genre_and_artist_can_be_removed_and_saved(self):
        self.finish()
        reply = self.genres()
        reply = self.click(reply, '✓ Драма')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Артисты')
        reply = self.click(reply, '✓ Анна Иванова')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Премьеры')
        reply = self.click(self.click(reply, 'Нет'), 'Завершить анкету')
        self.click(reply, 'Сохранить')
        self.assertEqual(self.db.execute("SELECT count(*) FROM preferences WHERE source='interest_survey'").fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT interested_in_premieres FROM subscribers').fetchone()[0], 0)

    def test_delete_cannot_target_another_user(self):
        self.finish()
        confirmation = self.click(self.send('Анкета интересов'), 'Удалить данные анкеты')
        before = self.count('preferences')
        self.user = '456'
        self.click(confirmation, 'Да, удалить')
        self.assertEqual(self.count('preferences'), before)

    def test_delete_without_profile_does_not_create_one(self):
        reply = self.click(self.send('Анкета интересов'), 'Удалить данные анкеты')
        self.click(reply, 'Да, удалить')
        self.assertEqual(self.count('subscribers'), 0)


    def test_clear_section_removes_even_inactive_selected_artist(self):
        self.finish()
        with self.theatre:
            self.theatre.execute("UPDATE artists SET is_active=0 WHERE source_url='artist-1'")
        reply = self.genres()
        reply = self.click(reply, 'Снять все отметки')
        reply = self.click(self.click(reply, 'К разделам анкеты'), 'Артисты')
        reply = self.click(reply, 'Снять все отметки')
        final = self.click(self.click(reply, 'К разделам анкеты'), 'Завершить анкету')
        self.click(final, 'Сохранить')
        self.assertEqual(self.db.execute("SELECT count(*) FROM preferences WHERE source='interest_survey'").fetchone()[0], 0)

    def test_max_manual_button_has_no_invitation_or_draft(self):
        self.channel = 'max'
        reply = self.send('Здравствуйте')
        self.assertEqual([b.label for b in reply.buttons], ['Анкета интересов','Обратная связь'])
        self.assertTrue(reply.keyboard_inline)
        self.assertNotIn('Хотите настроить интересы?', reply.reply.text)
        self.assertEqual(self.count('interest_survey_state'), 0)

    def test_delete_rolls_back_when_revocation_fails(self):
        import sqlite3
        self.finish()
        reply = self.click(self.send('Анкета интересов'), 'Удалить данные анкеты')
        before = [tuple(r) for r in self.db.execute('SELECT * FROM preferences')]
        with self.db:
            self.db.execute("CREATE TRIGGER fail_delete BEFORE INSERT ON consent_events WHEN NEW.action='revoked' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.click(reply, 'Да, удалить')
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM preferences')], before)
        self.assertEqual(self.db.execute('SELECT interested_in_premieres FROM subscribers').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT stage FROM interest_survey_state').fetchone()[0], 'delete_confirm')

    def test_keyboard_hint_only_once_and_survives_restart(self):
        first = self.send('Здравствуйте')
        self.assertIn('рядом с полем ввода нажмите кнопку', first.reply.text)
        self.assertIn('Мои интересы', first.reply.text)
        self.assertNotIn('рядом с полем ввода нажмите кнопку', self.send('Спасибо').reply.text)
        self.db.close()
        self.db = connect_subscribers(self.root/'subscribers.sqlite3')
        initialize_subscribers(self.db)
        self.handler = self.make_handler()
        self.assertNotIn('рядом с полем ввода нажмите кнопку', self.send('Здравствуйте').reply.text)
        self.assertEqual(self.count('channel_menu_hints'), 1)
        self.assertEqual(self.count('subscribers'), 0)
        self.assertEqual(self.count('interest_survey_state'), 0)

    def test_keyboard_hint_is_per_user_and_not_for_max(self):
        self.assertIn('рядом с полем ввода нажмите кнопку', self.send('Здравствуйте').reply.text)
        self.user = '456'
        self.assertIn('рядом с полем ввода нажмите кнопку', self.send('Здравствуйте').reply.text)
        self.channel = 'max'
        self.assertNotIn('рядом с полем ввода нажмите кнопку', self.send('Здравствуйте').reply.text)
        self.assertEqual(self.count('channel_menu_hints'), 2)

    def test_keyboard_hint_does_not_replace_date_or_survey_buttons(self):
        self.assertNotIn('рядом с полем ввода нажмите кнопку', self.send('Гамлет').reply.text)
        intro = self.send('Анкета интересов')
        self.assertNotIn('рядом с полем ввода нажмите кнопку', intro.reply.text)
        self.assertEqual(self.count('channel_menu_hints'), 0)
        self.click(intro, 'Пропустить')
        self.assertIn('рядом с полем ввода нажмите кнопку', self.send('Здравствуйте').reply.text)

    def test_keyboard_hint_does_not_append_to_stop(self):
        self.assertNotIn('рядом с полем ввода нажмите кнопку', self.send('Стоп').reply.text)
        self.assertEqual(self.count('channel_menu_hints'), 0)

if __name__ == '__main__':
    unittest.main()
