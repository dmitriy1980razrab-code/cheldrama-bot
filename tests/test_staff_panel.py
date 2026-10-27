from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
import tempfile
import threading
import unittest
import sqlite3
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer
from urllib.request import build_opener,HTTPCookieProcessor,Request
from urllib.error import HTTPError
from urllib.parse import urlencode
from http.cookiejar import CookieJar
from cryptography.fernet import Fernet
from theatre_bot.subscribers import connect_subscribers,initialize_subscribers,IdentityProtector,record_consent
from theatre_bot.database import connect,initialize
from theatre_bot.admin_access import create_staff,require,password_matches,set_staff_active
from theatre_bot.admin_panel import PanelSecurity,create_panel_handler,cancel_campaign,campaign_page,campaign_targets
from theatre_bot.feedback import FeedbackFlow,staff_reply,authorized_ticket,change_ticket,queue_staff_alerts
from theatre_bot.channels import IncomingMessage
from theatre_bot.marketing_reports import report_rows
from theatre_bot.interest_preferences import save_preferences
from theatre_bot.persistent_replies import claim_replies
from theatre_bot.campaigns import create_campaign,approve_campaign,schedule_campaign
from theatre_bot.campaign_delivery import queue_due_campaigns

class StaffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=connect_subscribers(self.root/'subscribers.sqlite3');initialize_subscribers(self.db);self.addCleanup(self.db.close)
        self.theatre=connect(self.root/'theatre.sqlite3');initialize(self.theatre);self.addCleanup(self.theatre.close)
        self.protector=IdentityProtector(Fernet.generate_key(),b'x'*32)
        self.now=datetime(2026,10,26,8,tzinfo=timezone.utc)
        self.ids={}
        for role in ('advertising','analytics','observer','support'):
            self.ids[role]=create_staff(self.db,role,'strong-password-123',role,now=self.now)
        self.flow=FeedbackFlow(self.db,self.protector);self.counter=0
        record_consent(self.db,self.protector,'vk','123','personal_data','draft-v1',True,'test',at=self.now)

    def user(self,role):return self.db.execute('SELECT * FROM admin_users WHERE id=?',(self.ids[role],)).fetchone()
    def msg(self,text='',action=None,user='123',channel='vk'):
        self.counter+=1
        return IncomingMessage(channel,user,str(self.counter),text,'Зритель',action)
    def send(self,text='',action=None,**kw):return self.flow.try_handle(self.msg(text,action,**kw),self.now)
    def click(self,r,label,**kw):return self.send(action=next(b.action for b in r.buttons if b.label==label),**kw)
    def ticket(self,department='advertising',channel='vk'):
        self.click(self.send(action='feedback:open',channel=channel),{'advertising':'Реклама, акции','social':'Соц.сети','technical':'Техподдержка'}[department],channel=channel)
        self.send('Вопрос зрителя',channel=channel)
        return self.db.execute('SELECT max(id) FROM feedback_tickets').fetchone()[0]

    def test_role_permissions(self):
        for role in ('analytics','observer','support'):
            with self.assertRaises(PermissionError):require(self.user(role),'campaigns')
            with self.assertRaises(PermissionError):require(self.user(role),'accounts')
        for role in ('advertising','analytics','observer'):require(self.user(role),'reports')
        with self.assertRaises(PermissionError):require(self.user('support'),'reports')

    def test_campaign_statistics_are_restricted_and_count_statuses(self):
        cid=create_campaign(self.db,'Акция','Текст','advertising',at=self.now)
        subscriber=self.db.execute('SELECT id FROM subscribers LIMIT 1').fetchone()[0]
        self.db.execute('INSERT INTO campaign_recipients (campaign_id,subscriber_id,status) VALUES (?,?,?)',(cid,subscriber,'sent'))
        page=campaign_page(self.db,self.theatre,self.user('advertising'),'token',False)
        self.assertIn('отправлено — 1',page)
        self.assertIn('запланировано — 0',page)
        self.assertIn('результаты акций пока не отслеживаются',page)
        for role in ('analytics','observer','support'):
            with self.assertRaises(PermissionError):
                campaign_page(self.db,self.theatre,self.user(role),'token',False)

    def test_campaign_targets_include_catalog_plays_without_subscribers(self):
        self.theatre.execute("INSERT INTO plays(source_url,title,normalized_title,synced_at) VALUES (?,?,?,?)",('https://example.test/play','Пример спектакля','пример спектакля',self.now.isoformat()))
        targets=campaign_targets(self.db,self.theatre)
        self.assertEqual(targets['play']['https://example.test/play'],'Пример спектакля')
        page=campaign_page(self.db,self.theatre,self.user('advertising'),'token',False)
        for group in ('Жанры','Спектакли','Артисты'):
            self.assertIn('<summary>'+group+'</summary>',page)
        self.assertIn('Пример спектакля',page)
        cid=create_campaign(self.db,'Проба','Текст','advertising',target_type='play',target_key='https://example.test/play',at=self.now)
        from theatre_bot.campaigns import preview_campaign
        self.assertEqual(preview_campaign(self.db,self.protector,cid,'advertising','test',at=self.now).total,0)

    def test_multiple_campaign_interests_match_once_and_season_expires(self):
        record_consent(self.db,self.protector,'vk','123','marketing','draft-v1',True,'test',at=self.now)
        subscriber=self.db.execute('SELECT id FROM subscribers LIMIT 1').fetchone()[0]
        with self.db:
            self.db.execute("UPDATE subscribers SET status='active' WHERE id=?",(subscriber,))
            self.db.execute("INSERT INTO preferences (subscriber_id,preference_type,preference_key,preference_label,source,recorded_at) VALUES (?,'genre','comedy','Комедия','interest_survey',?)",(subscriber,self.now.isoformat()))
            self.db.execute('INSERT INTO survey_play_interests VALUES (?,?,?,?,?)',(subscriber,'play1','Спектакль',2026,self.now.isoformat()))
        from theatre_bot.campaigns import preview_campaign
        cid=create_campaign(self.db,'Проба','Текст','advertising',targets=[('genre','comedy','Комедия'),('play','play1','Спектакль')],at=self.now)
        self.assertEqual(preview_campaign(self.db,self.protector,cid,'advertising','test',at=self.now).total,1)
        play_only=create_campaign(self.db,'Проба 2','Текст','advertising',targets=[('play','play1','Спектакль')],at=self.now)
        self.assertEqual(preview_campaign(self.db,self.protector,play_only,'advertising','test',at=self.now).total,1)
        self.assertEqual(preview_campaign(self.db,self.protector,play_only,'advertising','test',at=datetime(2027,7,11,8,tzinfo=timezone.utc)).total,0)

    def test_password_is_hashed_and_login_invalidated_on_disable(self):
        security=PanelSecurity();u=self.user('analytics')
        self.assertNotIn('strong-password',u['password_hash'])
        self.assertTrue(password_matches('strong-password-123',u['password_hash']))
        self.assertIsNone(security.login(self.db,'analytics','wrong'))
        token=security.login(self.db,'analytics','strong-password-123')
        self.assertIsNotNone(security.session(self.db,token))
        set_staff_active(self.db,self.user('advertising'),u['id'],False)
        self.assertIsNone(security.session(self.db,token))

    def test_session_expiry_login_throttling(self):
        security=PanelSecurity();token=security.login(self.db,'observer','strong-password-123',now=0)
        self.assertIsNone(security.session(self.db,token,now=1801))
        for _ in range(10):security.login(self.db,'none','wrong',now=2000)
        self.assertIsNone(security.login(self.db,'observer','strong-password-123',now=2001))
        self.assertIsNotNone(security.login(self.db,'observer','strong-password-123',now=2301))

    def test_cannot_disable_self(self):
        u=self.user('advertising')
        with self.assertRaises(ValueError):set_staff_active(self.db,u,u['id'],False)

    def test_department_routes_and_encrypts_text(self):
        tid=self.ticket('social')
        row=self.db.execute('SELECT * FROM feedback_tickets WHERE id=?',(tid,)).fetchone()
        self.assertEqual(row['department'],'social')
        raw=self.db.execute('SELECT body_encrypted FROM feedback_messages').fetchone()[0]
        self.assertNotIn('Вопрос'.encode(),raw)
        self.assertEqual(self.protector.decrypt(raw),'Вопрос зрителя')
        self.assertIsNotNone(authorized_ticket(self.db,self.user('analytics'),tid))
        for role in ('advertising','observer','support'):
            with self.assertRaises(PermissionError):authorized_ticket(self.db,self.user(role),tid)

    def test_no_consent_no_ticket(self):
        menu=self.send(action='feedback:open',user='other')
        reply=self.click(menu,'Техподдержка',user='other')
        self.assertIn('Согласен',[b.label for b in reply.buttons])
        self.send('Вопрос',user='other')
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_tickets').fetchone()[0],0)
        self.click(reply,'Согласен',user='other')
        self.send('Вопрос',user='other')
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_tickets').fetchone()[0],1)

    def test_foreign_and_stale_callbacks_do_not_create_ticket(self):
        menu=self.send(action='feedback:open')
        self.click(menu,'Техподдержка',user='other')
        self.now+=timedelta(minutes=31)
        self.click(menu,'Техподдержка')
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_tickets').fetchone()[0],0)

    def test_multiple_messages_same_ticket_and_return_to_bot(self):
        tid=self.ticket()
        reply=self.send('Ещё вопрос')
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_tickets').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_messages').fetchone()[0],2)
        self.click(reply,'Вернуться к боту')
        self.assertIsNone(self.send('Гамлет'))

    def test_survey_button_leaves_feedback(self):
        self.ticket()
        self.assertIsNone(self.send(action='survey:open'))
        self.assertIsNone(self.send('Драма'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_messages').fetchone()[0],1)

    def test_reply_is_atomic_deduplicated_and_in_original_channel(self):
        tid=self.ticket('technical')
        user=self.user('support')
        self.assertTrue(staff_reply(self.db,self.protector,user,tid,'Ответ','nonce',self.now))
        self.assertFalse(staff_reply(self.db,self.protector,user,tid,'Ответ','nonce',self.now))
        self.assertEqual(self.db.execute('SELECT count(*) FROM outgoing_reply_queue').fetchone()[0],1)
        queued=claim_replies(self.db,self.protector,now=self.now)
        self.assertEqual(queued[0].message.channel,'vk')
        self.assertEqual(queued[0].message.external_user_id,'123')
        self.assertIn('Ответ сотрудника',queued[0].message.reply.text)
        self.assertIsNotNone(self.send(action=f'feedback:resume:{tid}'))
        self.assertIn('недоступно',self.send(action=f'feedback:resume:{tid}',user='other').reply.text)

    def test_reply_rollback_on_queue_failure(self):
        tid=self.ticket('social')
        with self.db:self.db.execute("CREATE TRIGGER queue_fail BEFORE INSERT ON outgoing_reply_queue BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):staff_reply(self.db,self.protector,self.user('analytics'),tid,'Ответ','a',self.now)
        self.assertEqual(self.db.execute("SELECT count(*) FROM feedback_messages WHERE direction='outgoing'").fetchone()[0],0)

    def test_transfer_preserves_history_and_changes_permissions(self):
        tid=self.ticket('advertising')
        change_ticket(self.db,self.user('advertising'),tid,'transfer','social',self.now)
        with self.assertRaises(PermissionError):authorized_ticket(self.db,self.user('advertising'),tid)
        self.assertIsNotNone(authorized_ticket(self.db,self.user('analytics'),tid))
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_messages').fetchone()[0],1)
        change_ticket(self.db,self.user('analytics'),tid,'close',now=self.now)
        with self.assertRaises(ValueError):staff_reply(self.db,self.protector,self.user('analytics'),tid,'Ответ','a',self.now)

    def test_offline_alert_has_no_viewer_identity_or_text_and_does_not_repeat(self):
        self.ticket('social')
        with self.db:
            self.db.execute("UPDATE admin_users SET notify_channel='vk',notify_id_encrypted=? WHERE id=?",(self.protector.encrypt('staff'),self.ids['analytics']))
        self.assertEqual(queue_staff_alerts(self.db,self.protector,self.now),1)
        self.assertEqual(queue_staff_alerts(self.db,self.protector,self.now),0)
        payload=self.protector.decrypt(self.db.execute('SELECT payload_encrypted FROM outgoing_reply_queue').fetchone()[0])
        self.assertNotIn('Вопрос зрителя',payload)
        self.assertNotIn('Зритель',payload)
        self.assertIn('Соц.сети',payload)

    def test_online_and_disabled_staff_do_not_receive_alerts(self):
        self.ticket('social')
        with self.db:self.db.execute("UPDATE admin_users SET notify_channel='vk',notify_id_encrypted=?,last_seen_at=? WHERE id=?",(self.protector.encrypt('staff'),self.now.isoformat(),self.ids['analytics']))
        self.assertEqual(queue_staff_alerts(self.db,self.protector,self.now),0)
        set_staff_active(self.db,self.user('advertising'),self.ids['analytics'],False)
        self.assertEqual(queue_staff_alerts(self.db,self.protector,self.now+timedelta(minutes=10)),0)

    def test_preference_changes_and_observer_aggregate_report(self):
        save_preferences(self.db,self.theatre,self.protector,'vk','123','Зритель',['драма'],[],True,True,'draft-v1',now=self.now)
        self.assertEqual(self.db.execute('SELECT count(*) FROM preference_changes').fetchone()[0],2)
        save_preferences(self.db,self.theatre,self.protector,'vk','123','Зритель',[],[],False,True,'draft-v1',now=self.now)
        self.assertEqual(self.db.execute("SELECT count(*) FROM preference_changes WHERE action='removed'").fetchone()[0],2)
        rows=report_rows(self.db,self.user('observer'),'2026-10-01','2026-10-31')
        self.assertTrue(any('Изменения' in r[0] for r in rows))
        self.assertNotIn('Зритель',str(rows))
        with self.assertRaises(ValueError):report_rows(self.db,self.user('observer'),'2026-10-31','2026-10-01')

    def test_campaign_cancel_cancels_queue_and_keeps_audit(self):
        save_preferences(self.db,self.theatre,self.protector,'vk','123',None,[],[],False,True,'draft-v1',now=self.now)
        cid=create_campaign(self.db,'Название','Текст','advertising',at=self.now)
        approve_campaign(self.db,cid,'advertising',at=self.now)
        schedule_campaign(self.db,cid,self.now+timedelta(minutes=1),'advertising',at=self.now)
        queue_due_campaigns(self.db,self.protector,now=self.now+timedelta(minutes=2))
        cancel_campaign(self.db,self.user('advertising'),cid)
        self.assertEqual(self.db.execute('SELECT status FROM outgoing_reply_queue').fetchone()[0],'cancelled')
        self.assertEqual(self.db.execute('SELECT status FROM campaign_recipients').fetchone()[0],'skipped')

    def test_role_change_revokes_old_session(self):
        from theatre_bot.admin_access import set_staff_role
        security=PanelSecurity();token=security.login(self.db,'analytics','strong-password-123')
        set_staff_role(self.db,self.user('advertising'),self.ids['analytics'],'support')
        self.assertIsNone(security.session(self.db,token))
        self.assertEqual(self.user('analytics')['role'],'support')

    def test_disabled_staff_alert_is_cancelled_before_delivery(self):
        from theatre_bot.persistent_replies import deliver_persistent_replies
        class Sender:
            def __init__(self):self.messages=[]
            def send(self,message):self.messages.append(message)
        self.ticket('social')
        with self.db:self.db.execute("UPDATE admin_users SET notify_channel='vk',notify_id_encrypted=? WHERE id=?",(self.protector.encrypt('staff'),self.ids['analytics']))
        queue_staff_alerts(self.db,self.protector,self.now)
        set_staff_active(self.db,self.user('advertising'),self.ids['analytics'],False)
        sender=Sender();report=deliver_persistent_replies(self.db,self.protector,sender,now=self.now)
        self.assertEqual(report.cancelled,1)
        self.assertEqual(sender.messages,[])

    def test_transfer_generates_alert_for_new_department(self):
        tid=self.ticket('advertising')
        with self.db:
            self.db.execute("UPDATE admin_users SET notify_channel='vk',notify_id_encrypted=?,alert_cursor=100 WHERE id=?",(self.protector.encrypt('staff'),self.ids['analytics']))
            # Cursor can be newer than all messages in the transferred ticket.
            self.db.execute("INSERT INTO feedback_alert_events(id,ticket_id,created_at) VALUES(101,?,?)",(tid,self.now.isoformat()))
        change_ticket(self.db,self.user('advertising'),tid,'transfer','social',self.now)
        self.assertEqual(queue_staff_alerts(self.db,self.protector,self.now),1)

    def test_closed_history_retention_preserves_open_tickets(self):
        from theatre_bot.feedback import purge_feedback_history
        closed=self.ticket('advertising')
        change_ticket(self.db,self.user('advertising'),closed,'close',now=self.now)
        self.send(action='feedback:open')
        opened=self.ticket('technical')
        self.assertEqual(purge_feedback_history(self.db,self.now+timedelta(days=31)),1)
        self.assertEqual(self.db.execute('SELECT body_encrypted FROM feedback_messages WHERE ticket_id=?',(closed,)).fetchone()[0],b'')
        self.assertTrue(self.db.execute('SELECT body_encrypted FROM feedback_messages WHERE ticket_id=?',(opened,)).fetchone()[0])

    def test_expired_pairing_code_and_disabled_account_cannot_be_linked(self):
        import hashlib
        from theatre_bot.feedback import claim_staff_notifications
        with self.db:
            self.db.execute('INSERT INTO staff_notify_challenges(staff_id,code_hash,created_at,expires_at) VALUES(?,?,?,?)',
                (self.ids['analytics'],hashlib.sha256(b'code').hexdigest(),self.now.isoformat(),(self.now+timedelta(minutes=1)).isoformat()))
        self.assertIn('недействителен',claim_staff_notifications(self.db,self.protector,'vk','staff','code',self.now+timedelta(minutes=2)))
        set_staff_active(self.db,self.user('advertising'),self.ids['analytics'],False)
        self.assertIn('недействителен',claim_staff_notifications(self.db,self.protector,'vk','staff','code',self.now))

    def test_handler_feedback_and_stop_do_not_change_survey_or_subscription_consents(self):
        from theatre_bot.channel_handler import ChannelMessageHandler
        handler=ChannelMessageHandler(self.theatre,self.db,self.protector,survey_enabled=True)
        menu=handler.process(self.msg(action='feedback:open'),now=self.now)
        action=next(b.action for b in menu.buttons if b.label=='Соц.сети')
        handler.process(self.msg(action=action),now=self.now)
        reply=handler.process(self.msg('Вопрос о публикации'),now=self.now)
        self.assertIn('Обращение №',reply.reply.text)
        self.assertEqual(self.db.execute("SELECT count(*) FROM preferences").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM consent_events WHERE consent_type='marketing'").fetchone()[0],0)
        handler.process(self.msg('Стоп'),now=self.now)
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_sessions').fetchone()[0],0)

    def test_worker_sends_alert_without_running_campaigns_by_default(self):
        from theatre_bot.persistent_delivery_worker import PersistentDeliveryWorker
        class Sender:
            def __init__(self):self.messages=[]
            def send(self,message):self.messages.append(message)
        self.ticket('social')
        with self.db:self.db.execute("UPDATE admin_users SET notify_channel='vk',notify_id_encrypted=? WHERE id=?",(self.protector.encrypt('staff'),self.ids['analytics']))
        sender=Sender();worker=PersistentDeliveryWorker(self.root/'subscribers.sqlite3',self.protector,sender,auto_start=False)
        worker.run_once(self.now)
        self.assertEqual(len(sender.messages),1)
        self.assertEqual(sender.messages[0].external_user_id,'staff')
        worker.close()

    def test_two_due_campaigns_cannot_bypass_delivery_interval(self):
        from theatre_bot.persistent_replies import deliver_persistent_replies
        class Sender:
            def __init__(self):self.messages=[]
            def send(self,message):self.messages.append(message)
        save_preferences(self.db,self.theatre,self.protector,'vk','123',None,[],[],False,True,'draft-v1',now=self.now)
        for name in ('Первая','Вторая'):
            cid=create_campaign(self.db,name,'Текст','advertising',at=self.now)
            approve_campaign(self.db,cid,'advertising',at=self.now)
            schedule_campaign(self.db,cid,self.now+timedelta(minutes=1),'advertising',at=self.now)
        due=self.now+timedelta(minutes=2)
        queue_due_campaigns(self.db,self.protector,now=due)
        sender=Sender();report=deliver_persistent_replies(self.db,self.protector,sender,now=due)
        self.assertEqual(report.sent,1)
        self.assertEqual(report.cancelled,1)
        self.assertEqual(len(sender.messages),1)

    def test_max_feedback_response_preserves_channel(self):
        menu=self.send(action='feedback:open',channel='max')
        consent=self.click(menu,'Техподдержка',channel='max')
        self.click(consent,'Согласен',channel='max')
        self.send('Ошибка',channel='max')
        tid=self.db.execute('SELECT max(id) FROM feedback_tickets').fetchone()[0]
        staff_reply(self.db,self.protector,self.user('support'),tid,'Проверим','nonce',self.now)
        queued=claim_replies(self.db,self.protector,now=self.now)
        self.assertEqual(queued[0].message.channel,'max')
        self.assertEqual(queued[0].message.external_user_id,'123')

    def test_enabled_worker_queues_and_sends_due_campaign(self):
        from theatre_bot.persistent_delivery_worker import PersistentDeliveryWorker
        class Sender:
            def __init__(self):self.messages=[]
            def send(self,message):self.messages.append(message)
        save_preferences(self.db,self.theatre,self.protector,'vk','123',None,[],[],False,True,'draft-v1',now=self.now)
        cid=create_campaign(self.db,'Тест','Текст','advertising',at=self.now)
        approve_campaign(self.db,cid,'advertising',at=self.now)
        schedule_campaign(self.db,cid,self.now+timedelta(minutes=1),'advertising',at=self.now)
        sender=Sender();worker=PersistentDeliveryWorker(self.root/'subscribers.sqlite3',self.protector,sender,auto_start=False,campaigns_enabled=True)
        report=worker.run_once(self.now+timedelta(minutes=2))
        self.assertEqual(report.sent,1);self.assertEqual(len(sender.messages),1)
        worker.run_once(self.now+timedelta(minutes=3))
        self.assertEqual(len(sender.messages),1)
        worker.close()

    def test_vk_name_is_requested_once_without_creating_ticket_for_name(self):
        record_consent(self.db,self.protector,'vk','nameless','personal_data','draft-v1',True,'test',at=self.now)
        counter=0
        def send(text='',action=None):
            nonlocal counter
            counter+=1
            return self.flow.try_handle(IncomingMessage('vk','nameless','name-'+str(counter),text,None,action),self.now)
        menu=send(action='feedback:open')
        prompt=send(action=next(b.action for b in menu.buttons if b.label=='Техподдержка'))
        self.assertIn('Как к Вам обращаться',prompt.reply.text)
        send('Алексей')
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_tickets').fetchone()[0],0)
        row=self.db.execute("SELECT display_name_encrypted FROM subscribers WHERE external_id_hash=?",(self.protector.lookup_hash('vk','nameless'),)).fetchone()
        self.assertEqual(self.protector.decrypt(row[0]),'Алексей')
        send('Не работает кнопка')
        self.assertEqual(self.db.execute('SELECT count(*) FROM feedback_messages').fetchone()[0],1)
        menu=send(action='feedback:open')
        prompt=send(action=next(b.action for b in menu.buttons if b.label=='Техподдержка'))
        self.assertNotIn('Как к Вам обращаться',prompt.reply.text)

    def test_viewer_may_skip_name(self):
        record_consent(self.db,self.protector,'vk','nameless','personal_data','draft-v1',True,'test',at=self.now)
        menu=self.flow.try_handle(IncomingMessage('vk','nameless','s1','',None,'feedback:open'),self.now)
        self.flow.try_handle(IncomingMessage('vk','nameless','s2','',None,next(b.action for b in menu.buttons if b.label=='Техподдержка')),self.now)
        self.flow.try_handle(IncomingMessage('vk','nameless','s3','Пропустить'),self.now)
        reply=self.flow.try_handle(IncomingMessage('vk','nameless','s4','Вопрос'),self.now)
        self.assertIn('Обращение №',reply.reply.text)

    def test_name_prompt_is_available_for_every_feedback_department(self):
        for index,department in enumerate(('advertising','social','technical')):
            visitor='visitor-'+str(index)
            record_consent(self.db,self.protector,'vk',visitor,'personal_data','draft-v1',True,'test',at=self.now)
            menu=self.flow.try_handle(IncomingMessage('vk',visitor,'prompt1','',None,'feedback:open'),self.now)
            action=next(b.action for b in menu.buttons if b.label=={'advertising':'Реклама, акции','social':'Соц.сети','technical':'Техподдержка'}[department])
            reply=self.flow.try_handle(IncomingMessage('vk',visitor,'prompt2','',None,action),self.now)
            self.assertIn('Как к Вам обращаться',reply.reply.text)
            self.assertNotIn('вопрос техподдержке',reply.reply.text)

    def test_excel_show_dates_distinguish_performances(self):
        from theatre_bot.subscribers import subscribe_to_play
        record_consent(self.db,self.protector,'vk','123','service_notifications','draft-v1',True,'test',at=self.now)
        subscribe_to_play(self.db,self.protector,'vk','123','play-key','Гамлет',at=self.now,
            performance_key='performance-key',selected_starts_at='2026-11-20T18:00')
        rows=report_rows(self.db,self.user('observer'),'2026-10-01','2026-10-31')
        self.assertTrue(any('Гамлет · 20.11.2026 18:00'==row[2] for row in rows))

    def test_excel_marketing_people_and_consent_events_remain_distinct(self):
        record_consent(self.db,self.protector,'vk','123','marketing','draft-v1',True,'test',at=self.now)
        record_consent(self.db,self.protector,'vk','123','marketing','draft-v1',False,'test',at=self.now)
        with self.db:self.db.execute("UPDATE subscribers SET status='active'")
        rows=report_rows(self.db,self.user('observer'),'2026-10-01','2026-10-31')
        current=[r for r in rows if r[0]=='Рекламное согласие сейчас']
        self.assertEqual(current[0][3],0)
        events=[r for r in rows if r[0]=='Согласия за период' and r[1]=='Реклама']
        self.assertEqual(sum(r[3] for r in events),2)

class PanelHttpTests(StaffTests):
    # HTTP requests use separate connections, as in the actual loopback server.
    def setUp(self):
        super().setUp()
        self.security=PanelSecurity()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),create_panel_handler(self.root/'subscribers.sqlite3',self.root/'theatre.sqlite3',self.protector,self.security))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop_server)
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.cookies=CookieJar();self.client=build_opener(HTTPCookieProcessor(self.cookies))
    def stop_server(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,data=None):
        req=Request(self.url+path,data=urlencode(data).encode() if data is not None else None)
        try:
            with self.client.open(req,timeout=5) as response:return response.status,response.read().decode('utf-8-sig')
        except HTTPError as error:return error.code,error.read().decode()
    def login(self,role):
        status,_=self.request('/login',{'username':role,'password':'strong-password-123'})
        self.assertEqual(status,200)
        token=next(c.value for c in self.cookies if c.name=='staff_session')
        return self.security.sessions[token]['csrf']
    def test_campaign_multiple_choices_preview_as_single_campaign(self):
        with self.theatre:
            self.theatre.execute("INSERT INTO plays(source_url,title,normalized_title,synced_at) VALUES (?,?,?,?)",('https://example.test/play','Спектакль','спектакль',self.now.isoformat()))
        csrf=self.login('advertising')
        status,body=self.request('/campaign/create',{'csrf':csrf,'name':'Проба','message':'Текст','channel':'vk',
            'choice_1':'genre|комедия','choice_2':'play|https://example.test/play'})
        self.assertEqual(status,200)
        self.assertIn('Спектакль',body)
        self.assertIn('Комедия',body)
        self.assertEqual(self.db.execute('SELECT count(*) FROM campaign_targets').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM outgoing_reply_queue').fetchone()[0],0)
    def test_observer_cannot_access_audience_tickets_campaigns_or_post(self):
        csrf=self.login('observer')
        self.assertEqual(self.request('/reports')[0],200)
        for path in ('/audience','/tickets','/campaigns','/accounts','/audit'):
            self.assertEqual(self.request(path)[0],403)
        self.assertEqual(self.request('/campaign/create',{'csrf':csrf,'name':'n','message':'t','target':'all|'})[0],403)
    def test_missing_csrf_cannot_create_account(self):
        self.login('advertising')
        status,_=self.request('/account/create',{'username':'new','password':'strong-password-123','role':'observer'})
        self.assertEqual(status,403)
        self.assertEqual(self.db.execute('SELECT count(*) FROM admin_users').fetchone()[0],4)
    def test_ticket_html_escaped_and_role_scoped(self):
        tid=self.ticket('social')
        with self.db:self.db.execute('UPDATE feedback_messages SET body_encrypted=?',(self.protector.encrypt('<script>alert(1)</script>'),))
        self.login('analytics')
        status,body=self.request('/ticket?id='+str(tid))
        self.assertEqual(status,200);self.assertIn('&lt;script&gt;',body);self.assertNotIn('<script>alert(1)</script>',body)
        self.assertEqual(self.request('/accounts')[0],403)
        self.login('support');self.assertEqual(self.request('/ticket?id='+str(tid))[0],403)
    def test_notification_pairing_requires_private_code_and_actual_chat(self):
        self.now=datetime.now(timezone.utc)
        csrf=self.login('analytics')
        status,body=self.request('/profile/notify',{'csrf':csrf})
        self.assertEqual(status,200)
        self.assertIsNone(self.user('analytics')['notify_id_encrypted'])
        import re
        command=re.search(r'<pre>(Подключить уведомления [^<]+)</pre>',body).group(1)
        self.assertIn('недействителен',self.send('Подключить уведомления wrong',user='employee').reply.text)
        self.assertIn('подключены',self.send(command,user='employee').reply.text)
        self.assertEqual(self.protector.decrypt(self.user('analytics')['notify_id_encrypted']),'employee')
        self.assertIn('недействителен',self.send(command,user='other').reply.text)
        self.assertEqual(self.db.execute('SELECT count(*) FROM outgoing_reply_queue').fetchone()[0],0)
    def test_campaign_preview_does_not_send_and_schedule_disabled(self):
        csrf=self.login('advertising')
        status,body=self.request('/campaign/create',{'csrf':csrf,'name':'Проба','message':'Текст','channel':'vk','target':'all|'})
        self.assertEqual(status,200);self.assertIn('Получателей сейчас',body)
        self.assertEqual(self.db.execute('SELECT count(*) FROM outgoing_reply_queue').fetchone()[0],0)
        cid=self.db.execute('SELECT id FROM campaigns').fetchone()[0]
        self.assertEqual(self.request('/campaign/approve',{'csrf':csrf,'id':cid,'confirm':'yes'})[0],200)
        self.assertEqual(self.request('/campaign/schedule',{'csrf':csrf,'id':cid,'confirm':'yes','scheduled_at':'2030-01-01T18:00'})[0],400)

    def test_http_staff_reply_shows_delivery_state(self):
        tid=self.ticket('social')
        csrf=self.login('analytics')
        status,body=self.request('/ticket/reply',{'csrf':csrf,'id':tid,'message':'Проверим вопрос','request_token':'http-once'})
        self.assertEqual(status,200)
        self.assertIn('Проверим вопрос',body)
        self.assertIn('Ожидает отправки',body)
        self.assertEqual(self.db.execute('SELECT count(*) FROM outgoing_reply_queue').fetchone()[0],1)
        self.request('/ticket/reply',{'csrf':csrf,'id':tid,'message':'Проверим вопрос','request_token':'http-once'})
        self.assertEqual(self.db.execute('SELECT count(*) FROM outgoing_reply_queue').fetchone()[0],1)

    def test_support_sees_escaped_name_without_identity_controls(self):
        tid=self.ticket('technical')
        with self.db:self.db.execute('UPDATE subscribers SET display_name_encrypted=?,external_id_encrypted=?',
            (self.protector.encrypt('Анна <b>Тест</b>'),self.protector.encrypt('unique-visitor-id')))
        csrf=self.login('support')
        status,body=self.request('/ticket?id='+str(tid))
        self.assertEqual(status,200)
        self.assertIn('Анна &lt;b&gt;Тест&lt;/b&gt;',body)
        self.assertNotIn('Показать ID',body)
        self.assertNotIn('unique-visitor-id',body)
        self.assertEqual(self.request('/ticket/identity',{'csrf':csrf,'id':tid})[0],403)
        self.assertEqual(self.request('/audience')[0],403)
        self.assertEqual(self.db.execute("SELECT count(*) FROM subscriber_access_log WHERE purpose LIKE '%:id'").fetchone()[0],0)

    def test_support_cannot_reveal_id_in_another_department(self):
        tid=self.ticket('social')
        csrf=self.login('support')
        self.assertEqual(self.request('/ticket/identity',{'csrf':csrf,'id':tid})[0],403)
        self.assertEqual(self.db.execute('SELECT count(*) FROM subscriber_access_log').fetchone()[0],0)

    def test_missing_author_name_has_neutral_label(self):
        tid=self.ticket('technical')
        with self.db:self.db.execute('UPDATE subscribers SET display_name_encrypted=NULL')
        self.login('support')
        status,body=self.request('/ticket?id='+str(tid))
        self.assertEqual(status,200);self.assertIn('Имя не указано',body)

    def test_every_department_sees_name_and_cannot_reveal_id_from_ticket(self):
        for role,department in [('advertising','advertising'),('analytics','social'),('support','technical')]:
            tid=self.ticket(department)
            csrf=self.login(role)
            status,body=self.request('/ticket?id='+str(tid))
            self.assertEqual(status,200)
            self.assertIn('Автор: <strong>Зритель</strong>',body)
            self.assertNotIn('Показать ID',body)
            self.assertNotIn('ID: <strong>',body)
            self.assertEqual(self.request('/ticket/identity',{'csrf':csrf,'id':tid})[0],403)
            foreign=self.ticket('social' if department!='social' else 'technical')
            self.assertEqual(self.request('/ticket?id='+str(foreign))[0],403)
        self.assertEqual(self.db.execute("SELECT count(*) FROM subscriber_access_log WHERE purpose LIKE '%:id'").fetchone()[0],0)

    def test_only_advertising_has_audience_identity_access(self):
        self.ticket('advertising')
        with self.db:self.db.execute("UPDATE subscribers SET status='active',external_id_encrypted=?",(self.protector.encrypt('audience-known-id'),))
        self.login('advertising')
        status,body=self.request('/audience')
        self.assertEqual(status,200)
        self.assertIn('<th>ID</th>',body)
        self.assertIn('audience-known-id',body)
        for role in ('analytics','observer','support'):
            self.login(role)
            self.assertEqual(self.request('/audience')[0],403)

    def test_login_page_preserves_origin_for_own_forms(self):
        with self.client.open(self.url+'/login',timeout=5) as response:
            self.assertEqual(response.headers.get('Referrer-Policy'),'same-origin')
        request=Request(self.url+'/login',data=urlencode({'username':'advertising','password':'strong-password-123'}).encode(),
                        headers={'Origin':self.url})
        with self.client.open(request,timeout=5) as response:
            self.assertEqual(response.status,200)
            self.assertIn('Реклама / администратор',response.read().decode())
        self.assertTrue(any(cookie.name=='staff_session' for cookie in self.cookies))

    def test_login_still_rejects_foreign_and_null_origins(self):
        for origin in ('null','https://foreign.example'):
            request=Request(self.url+'/login',data=urlencode({'username':'advertising','password':'strong-password-123'}).encode(),
                            headers={'Origin':origin})
            with self.assertRaises(HTTPError) as caught:
                self.client.open(request,timeout=5)
            self.assertEqual(caught.exception.code,403)
        self.assertEqual(len(self.security.sessions),0)

    def test_excel_export_download_has_correct_type_and_native_table(self):
        import io,zipfile
        self.login('observer')
        with self.client.open(self.url+'/reports.xlsx?period=season&date=2026-10-26',timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            self.assertIn('.xlsx',response.headers.get('Content-Disposition',''))
            with zipfile.ZipFile(io.BytesIO(response.read())) as book:
                self.assertIn('xl/worksheets/sheet1.xml',book.namelist())
        self.login('support')
        self.assertEqual(self.request('/reports.xlsx')[0],403)

    def test_excel_categories_are_also_separated_in_panel(self):
        self.login('observer')
        status,body=self.request('/reports')
        self.assertEqual(status,200)
        for heading in ('Аудитория','Интересы','Подписки на показы','Реклама','Согласия на уведомления','Обращения'):
            self.assertIn('<h3>'+heading+'</h3>',body)
        self.assertIn('один зритель может совершить несколько действий',body)

    def test_report_filters_http_download_only_selected_items(self):
        import io,zipfile
        from xml.etree import ElementTree as ET
        from urllib.parse import urlencode
        now=datetime.now(timezone.utc)
        date=(now+timedelta(hours=5)).date().isoformat()
        with self.db:
            for key,title in [('p1','Гамлет'),('p2','Обломов')]:
                self.db.execute('''INSERT INTO subscriptions(subscriber_id,topic_type,topic_key,topic_label,subscribed_at)
                    VALUES(1,'play',?,?,?)''',(key,title,now.isoformat(timespec='seconds')))
            for key,label in [('g1','Комедия'),('g2','Драма')]:
                self.db.execute('''INSERT INTO preferences(subscriber_id,preference_type,preference_key,preference_label,source,recorded_at)
                    VALUES(1,'genre',?,?,'interest_survey',?)''',(key,label,now.isoformat(timespec='seconds')))
        self.login('observer')
        query=urlencode({'period':'custom','from':date,'to':date,'play':'Гамлет','genre':'Комедия'})
        status,body=self.request('/reports?'+query)
        self.assertEqual(status,200)
        self.assertIn('value="Гамлет" selected',body)
        self.assertIn('value="Комедия" selected',body)
        with self.client.open(self.url+'/reports.xlsx?'+query,timeout=5) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as z:
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                def texts(index):
                    root=ET.fromstring(z.read(f'xl/worksheets/sheet{index}.xml'))
                    return [n.text or '' for n in root.findall('.//s:t',ns)]
                self.assertIn('Комедия',texts(2));self.assertNotIn('Драма',texts(2))
                self.assertIn('Гамлет',texts(3));self.assertNotIn('Обломов',texts(3))
                self.assertTrue(any('Спектакль: Гамлет; жанр: Комедия' in x for x in texts(3)))

    def test_report_filter_csv_metadata_cannot_execute_as_formula(self):
        import csv,io
        from urllib.parse import urlencode
        self.login('observer')
        query=urlencode({'play':'=1+2','genre':'Комедия'})
        with self.client.open(self.url+'/reports.csv?'+query,timeout=5) as response:
            rows=list(csv.reader(io.StringIO(response.read().decode('utf-8-sig'))))
        self.assertEqual(rows[2][1],"'=1+2")
        self.assertEqual(rows[2][3],'Комедия')
