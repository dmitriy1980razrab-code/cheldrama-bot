"""Explicit messenger feedback, encrypted tickets and role-separated staff replies."""
from datetime import datetime, timedelta, timezone
import secrets
import hashlib
from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.admin_access import DEPARTMENTS, ROLE_DEPARTMENTS, require, audit, stamp
from theatre_bot.interest_preferences import personal_data_granted
from theatre_bot.subscribers import _get_or_create_subscriber, record_consent
from theatre_bot.subscription_flow import DOCUMENT_VERSIONS, legal_documents_reply
from theatre_bot.persistent_replies import enqueue_reply, _transaction
from theatre_bot.spam_protection import normalize_message


def moment(now=None):
    value=now or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class FeedbackFlow:
    def __init__(self, db, protector):
        self.db,self.protector=db,protector

    def actor(self,m):
        return self.protector.lookup_hash(m.channel,'feedback:'+m.external_user_id)

    def state(self,m,now=None):
        return self.db.execute('SELECT * FROM feedback_sessions WHERE actor_hash=? AND expires_at>?',
                               (self.actor(m),stamp(now))).fetchone()

    def close(self,m):
        with self.db:
            self.db.execute('DELETE FROM feedback_sessions WHERE actor_hash=?',(self.actor(m),))

    def set_state(self,m,stage,now=None,department=None,ticket_id=None):
        token=secrets.token_urlsafe(12)
        with self.db:
            self.db.execute('''INSERT INTO feedback_sessions(actor_hash,stage,token,department,ticket_id,expires_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(actor_hash) DO UPDATE SET stage=excluded.stage,
                token=excluded.token,department=excluded.department,ticket_id=excluded.ticket_id,expires_at=excluded.expires_at''',
                (self.actor(m),stage,token,department,ticket_id,stamp(moment(now)+timedelta(minutes=30))))
        return token

    def reply(self,m,text,token=None,buttons=()):
        return OutgoingMessage(m.channel,m.external_user_id,Reply(text),tuple(
            OutgoingButton(f'feedback:{token}:{key}',label) for key,label in buttons))

    def menu(self,m,now=None):
        token=self.set_state(m,'menu',now)
        return self.reply(m,'Выберите тему обращения. Ответ сотрудника придёт в этот чат.',token,
                          list(DEPARTMENTS.items())+[('back','Вернуться к боту')])

    def protected(self,m,now=None):
        state=self.state(m,now)
        return bool(state and (m.action==f"feedback:{state['token']}:back" or
                    (not m.action and normalize_message(m.text) in {'отмена','вернуться к боту'})))

    def try_handle(self,m,now=None):
        text=normalize_message(m.text)
        if not m.action and text.startswith('подключить уведомления '):
            code=m.text.strip().split(None,2)[-1]
            return self.reply(m, claim_staff_notifications(self.db,self.protector,m.channel,m.external_user_id,code,now))
        if m.action=='feedback:open' or (not m.action and text=='обратная связь'):
            return self.menu(m,now)
        state=self.state(m,now)
        if m.action and not m.action.startswith('feedback:'):
            self.close(m)
            return None
        if state and not m.action and text in {'отмена','вернуться к боту'}:
            self.close(m)
            return self.reply(m,'Вы вернулись к боту. Можно задать вопрос о спектаклях.')
        if m.action and m.action.startswith('feedback:resume:'):
            try: ticket_id=int(m.action.split(':')[2])
            except ValueError: return self.reply(m,'Обращение недоступно.')
            ticket=self.db.execute('''SELECT t.* FROM feedback_tickets t JOIN subscribers s ON s.id=t.subscriber_id
                WHERE t.id=? AND s.channel=? AND s.external_id_hash=?''',
                (ticket_id,m.channel,self.protector.lookup_hash(m.channel,m.external_user_id))).fetchone()
            if not ticket or ticket['status']=='closed':
                return self.reply(m,'Обращение закрыто или недоступно. Нажмите «Обратная связь» для нового обращения.')
            token=self.set_state(m,'writing',now,ticket['department'],ticket_id)
            return self.reply(m,f'Напишите сообщение сотруднику по обращению №{ticket_id}.',token,[('back','Вернуться к боту')])
        if m.action and m.action.startswith('feedback:'):
            if not state or not m.action.startswith(f"feedback:{state['token']}:"):
                return self.reply(m,'Кнопка устарела. Откройте «Обратная связь» заново.')
            action=m.action.split(':',2)[2]
            if action=='back':
                self.close(m)
                return self.reply(m,'Вы вернулись к боту. Можно задать вопрос о спектаклях.')
            if state['stage']=='menu' and action in DEPARTMENTS:
                if not personal_data_granted(self.db,self.protector,m.channel,m.external_user_id):
                    token=self.set_state(m,'consent',now,action)
                    return self.reply(m,'Для передачи сообщения сотруднику нужно согласие на обработку персональных данных. '
                        'Рекламное согласие не требуется.\n\n'+legal_documents_reply().text,token,
                        [('accept','Согласен'),('back','Вернуться к боту')])
                token=self.set_state(m,'writing',now,action)
                return self.reply(m,'Напишите Ваш вопрос. Следующие сообщения будут передаваться сотруднику, '
                    'пока Вы не нажмёте «Вернуться к боту» (или 30 минут не будете писать).',token,[('back','Вернуться к боту')])
            if state['stage']=='consent' and action=='accept':
                record_consent(self.db,self.protector,m.channel,m.external_user_id,'personal_data',
                    DOCUMENT_VERSIONS['personal_data'],True,'feedback',at=now,display_name=m.display_name)
                token=self.set_state(m,'writing',now,state['department'])
                return self.reply(m,'Напишите Ваш вопрос сотруднику. Для обычных вопросов боту нажмите «Вернуться к боту».',
                                  token,[('back','Вернуться к боту')])
            return self.reply(m,'Действие недоступно. Откройте «Обратная связь» заново.')
        if not state or state['stage']!='writing' or m.action:
            return None
        # Other explicit bot commands always leave the staff conversation.
        if text in {'анкета интересов','мои интересы','интересы','изменить интересы'}:
            self.close(m)
            return None
        if not m.text.strip(): return self.reply(m,'Напишите вопрос текстом.')
        if not personal_data_granted(self.db,self.protector,m.channel,m.external_user_id):
            self.close(m)
            return self.reply(m,'Для обращения откройте «Обратная связь» и подтвердите согласие на обработку данных.')
        with _transaction(self.db):
            sid=_get_or_create_subscriber(self.db,self.protector,m.channel,m.external_user_id,now,m.display_name)
            tid=state['ticket_id']
            if tid:
                ticket=self.db.execute('SELECT status FROM feedback_tickets WHERE id=?',(tid,)).fetchone()
                if not ticket or ticket['status']=='closed': tid=None
            if not tid:
                tid=self.db.execute('''INSERT INTO feedback_tickets(subscriber_id,department,created_at,updated_at)
                    VALUES(?,?,?,?)''',(sid,state['department'],stamp(now),stamp(now))).lastrowid
            dedupe=self.protector.lookup_hash(m.channel,'feedback-event:'+m.external_message_id)
            inserted=self.db.execute('''INSERT OR IGNORE INTO feedback_messages(ticket_id,direction,body_encrypted,dedupe_key,created_at)
                VALUES(?,'incoming',?,?,?)''',(tid,self.protector.encrypt(m.text.strip()),dedupe,stamp(now))).rowcount
            if inserted:
                self.db.execute('INSERT INTO feedback_alert_events(ticket_id,created_at) VALUES (?,?)',(tid,stamp(now)))
            self.db.execute("UPDATE feedback_tickets SET updated_at=? WHERE id=?",(stamp(now),tid))
            self.db.execute('UPDATE feedback_sessions SET ticket_id=?,expires_at=? WHERE actor_hash=?',
                (tid,stamp(moment(now)+timedelta(minutes=30)),self.actor(m)))
        return self.reply(m,f'Обращение №{tid} передано сотруднику: {DEPARTMENTS[state["department"]]}. '
            'Ответ придёт в этот чат.',state['token'],[('back','Вернуться к боту')])


def authorized_ticket(db,user,ticket_id):
    require(user,'tickets')
    ticket=db.execute('SELECT * FROM feedback_tickets WHERE id=? AND department=?',
                     (ticket_id,ROLE_DEPARTMENTS[user['role']])).fetchone()
    if not ticket: raise PermissionError('Обращение недоступно')
    return ticket


def staff_reply(db,protector,user,ticket_id,text,request_token,now=None):
    text=text.strip()
    if not text or len(text)>2000 or not request_token or len(request_token)>128:
        raise ValueError('Ответ должен содержать от 1 до 2000 символов')
    with _transaction(db):
        ticket=authorized_ticket(db,user,ticket_id)
        if ticket['status']=='closed': raise ValueError('Обращение закрыто')
        if ticket['assigned_to'] not in (None,user['id']): raise ValueError('Обращение у другого сотрудника')
        subscriber=db.execute('SELECT * FROM subscribers WHERE id=?',(ticket['subscriber_id'],)).fetchone()
        if not subscriber['external_id_encrypted']: raise ValueError('Адрес доставки недоступен')
        key=f'staff-reply:{ticket_id}:{user["id"]}:{request_token}'
        encrypted_key=protector.lookup_hash(subscriber['channel'],key)
        inserted=db.execute('''INSERT OR IGNORE INTO feedback_messages
            (ticket_id,direction,body_encrypted,staff_id,dedupe_key,created_at) VALUES(?,'outgoing',?,?,?,?)''',
            (ticket_id,protector.encrypt(text),user['id'],encrypted_key,stamp(now))).rowcount
        if not inserted: return False
        outgoing=OutgoingMessage(subscriber['channel'],protector.decrypt(subscriber['external_id_encrypted']),
            Reply(f'Ответ сотрудника · {DEPARTMENTS[ticket["department"]]} · обращение №{ticket_id}\n\n{text}'),
            (OutgoingButton(f'feedback:resume:{ticket_id}','Ответить сотруднику'),))
        enqueue_reply(db,protector,outgoing,key,at=now)
        queue_hash=protector.lookup_hash(subscriber['channel'],'outgoing-reply:'+key)
        queue_id=db.execute('SELECT id FROM outgoing_reply_queue WHERE dedupe_hash=?',(queue_hash,)).fetchone()[0]
        db.execute('UPDATE feedback_messages SET outgoing_queue_id=? WHERE dedupe_key=?',(queue_id,encrypted_key))
        db.execute("UPDATE feedback_tickets SET status='working',assigned_to=?,updated_at=? WHERE id=?",(user['id'],stamp(now),ticket_id))
        audit(db,user['username'],'ticket_reply',ticket_id,now)
    return True


def change_ticket(db,user,ticket_id,action,department=None,now=None):
    with _transaction(db):
        ticket=authorized_ticket(db,user,ticket_id)
        if action=='take':
            if ticket['assigned_to'] not in (None,user['id']): raise ValueError('Обращение уже взято в работу')
            db.execute("UPDATE feedback_tickets SET status='working',assigned_to=?,updated_at=? WHERE id=?",(user['id'],stamp(now),ticket_id))
        elif action=='close':
            if ticket['assigned_to'] not in (None,user['id']): raise ValueError('Обращение у другого сотрудника')
            db.execute("UPDATE feedback_tickets SET status='closed',updated_at=? WHERE id=?",(stamp(now),ticket_id))
        elif action=='transfer' and department in DEPARTMENTS:
            if ticket['assigned_to'] not in (None,user['id']): raise ValueError('Обращение у другого сотрудника')
            db.execute("UPDATE feedback_tickets SET department=?,status='new',assigned_to=NULL,updated_at=? WHERE id=?",(department,stamp(now),ticket_id))
            db.execute('INSERT INTO feedback_alert_events(ticket_id,created_at) VALUES (?,?)',(ticket_id,stamp(now)))
        else: raise ValueError('Действие недоступно')
        audit(db,user['username'],'ticket_'+action,ticket_id,now)


def queue_staff_alerts(db,protector,now=None):
    """Queue metadata-only alerts when staff have not visited the panel for two minutes."""
    cutoff=stamp(moment(now)-timedelta(minutes=2))
    count=0
    with _transaction(db):
        users=db.execute('''SELECT * FROM admin_users WHERE active=1 AND notify_id_encrypted IS NOT NULL
            AND (last_seen_at IS NULL OR last_seen_at<?)''',(cutoff,)).fetchall()
        for user in users:
            department=ROLE_DEPARTMENTS.get(user['role'])
            if not department: continue
            rows=db.execute('''SELECT DISTINCT t.id FROM feedback_tickets t JOIN feedback_alert_events e ON e.ticket_id=t.id
                WHERE t.department=? AND t.status!='closed' AND e.id>?
                  AND (t.assigned_to IS NULL OR t.assigned_to=?)
                ORDER BY t.id LIMIT 50''',(department,user['alert_cursor'],user['id'])).fetchall()
            if not rows: continue
            latest=db.execute("SELECT COALESCE(MAX(id),0) FROM feedback_alert_events").fetchone()[0]
            numbers=', '.join(str(r['id']) for r in rows)
            outgoing=OutgoingMessage(user['notify_channel'],protector.decrypt(user['notify_id_encrypted']),
                Reply(f'Новые сообщения · {DEPARTMENTS[department]}. Обращения: {numbers}. Откройте раздел «Обращения» в панели.'))
            count+=int(enqueue_reply(db,protector,outgoing,f'staff-alert:{user["id"]}:{latest}',at=now,source_key=f'staff-alert:{user["id"]}:{user["role"]}:{hashlib.sha256(user["notify_id_encrypted"]).hexdigest()}'))
            db.execute('UPDATE admin_users SET alert_cursor=? WHERE id=?',(latest,user['id']))
    return count


def purge_feedback_history(db,now=None):
    """Clear closed ticket bodies after 30 days; retain routing and delivery metadata."""
    cutoff=stamp(moment(now)-timedelta(days=30))
    with _transaction(db):
        db.execute("DELETE FROM feedback_sessions WHERE expires_at<=?",(stamp(now),))
        db.execute("DELETE FROM staff_notify_challenges WHERE expires_at<=?",(stamp(now),))
        return db.execute("""UPDATE feedback_messages SET body_encrypted=X'' WHERE length(body_encrypted)>0
            AND ticket_id IN (SELECT id FROM feedback_tickets WHERE status='closed' AND updated_at<?)""",(cutoff,)).rowcount


def claim_staff_notifications(db,protector,channel,external_id,code,now=None):
    digest=hashlib.sha256(code.encode()).hexdigest()
    with _transaction(db):
        row=db.execute("""SELECT c.staff_id,u.role FROM staff_notify_challenges c JOIN admin_users u ON u.id=c.staff_id
            WHERE c.code_hash=? AND c.expires_at>? AND u.active=1""",(digest,stamp(now))).fetchone()
        if not row or row['role'] not in ROLE_DEPARTMENTS:
            return 'Код недействителен или истёк. Получите новый код в своём профиле панели.'
        db.execute('UPDATE admin_users SET notify_channel=?,notify_id_encrypted=? WHERE id=?',
                   (channel,protector.encrypt(external_id),row['staff_id']))
        db.execute('DELETE FROM staff_notify_challenges WHERE staff_id=?',(row['staff_id'],))
        audit(db,'staff:'+str(row['staff_id']),'notifications_enabled',now=now)
    return 'Оповещения о новых обращениях подключены к этому чату. Отключить их можно в профиле панели.'
