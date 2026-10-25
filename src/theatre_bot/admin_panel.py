"""Private staff panel. Bind to loopback and use SSH forwarding; never expose directly."""
from datetime import datetime, timedelta, timezone
from html import escape as esc
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs,urlparse
from pathlib import Path
import csv
import hashlib
import io
import secrets
import sqlite3
import threading
import time

from theatre_bot.admin_demo import _page, parse_scheduled_at
from theatre_bot.admin_access import (ROLES,DEPARTMENTS,ROLE_DEPARTMENTS,require,audit,stamp,
    create_staff,password_matches,password_hash,set_staff_active,set_staff_role)
from theatre_bot.feedback import authorized_ticket,staff_reply,change_ticket
from theatre_bot.marketing_reports import report_rows,report_period,comparison_rows
from theatre_bot.campaigns import create_campaign,preview_campaign,approve_campaign,schedule_campaign
from theatre_bot.persistent_replies import _transaction
from theatre_bot.subscribers import (connect_subscribers,initialize_subscribers,_build_profile)
from theatre_bot.database import connect


STATUS_NAMES={'new':'Новое','working':'В работе','closed':'Закрыто','draft':'Черновик',
    'approved':'Подтверждена','scheduled':'Запланирована','cancelled':'Отменена','completed':'Завершена',
    'pending':'Ожидает отправки','processing':'Отправляется','sent':'Отправлено','failed':'Ошибка','skipped':'Пропущено'}


def form(action,csrf,body):
    return f'<form method="post" action="{action}"><input type="hidden" name="csrf" value="{esc(csrf)}">{body}</form>'


def hidden(name,value):
    return f'<input type="hidden" name="{esc(name)}" value="{esc(str(value))}">'


def table(headers,rows):
    return '<table><thead><tr>'+''.join('<th>'+esc(str(h))+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+esc(str(x if x is not None else '—'))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'


class PanelSecurity:
    def __init__(self):
        self.sessions={}
        self.attempts=[]
        self.lock=threading.Lock()
        self.dummy=password_hash(secrets.token_urlsafe(24))

    def login(self,db,username,password,now=None):
        current=time.monotonic() if now is None else now
        with self.lock:
            self.attempts=[v for v in self.attempts if v>current-300]
            self.sessions={k:v for k,v in self.sessions.items() if v['expires']>current}
            if len(self.attempts)>=10 or len(self.sessions)>=100: return None
            self.attempts.append(current)
            user=db.execute('SELECT * FROM admin_users WHERE username=?',(username,)).fetchone()
            matched=password_matches(password,user['password_hash'] if user else self.dummy)
            if not user or not user['active'] or not matched:
                with db: audit(db,'login','login_failed')
                return None
            token=secrets.token_urlsafe(32)
            self.sessions[token]={'id':user['id'],'csrf':secrets.token_urlsafe(32),'expires':current+1800,
                                  'hash':user['password_hash'],'role':user['role']}
            with db: audit(db,user['username'],'login')
            return token

    def session(self,db,token,now=None):
        current=time.monotonic() if now is None else now
        with self.lock:
            session=self.sessions.get(token)
            if not session: return None
            user=db.execute('SELECT * FROM admin_users WHERE id=?',(session['id'],)).fetchone()
            if (session['expires']<=current or not user or not user['active']
                or user['password_hash']!=session['hash'] or user['role']!=session['role']):
                self.sessions.pop(token,None)
                return None
            return user,session['csrf']

    def logout(self,token):
        with self.lock: self.sessions.pop(token,None)


def cancel_campaign(db,user,campaign_id):
    require(user,'campaigns')
    with _transaction(db):
        changed=db.execute("UPDATE campaigns SET status='cancelled' WHERE id=? AND status IN ('draft','approved','scheduled')",(campaign_id,)).rowcount
        if not changed: raise ValueError('Кампанию нельзя отменить')
        recipient_ids=[str(r[0]) for r in db.execute("SELECT id FROM campaign_recipients WHERE campaign_id=?",(campaign_id,))]
        for rid in recipient_ids:
            db.execute("UPDATE outgoing_reply_queue SET status='cancelled',locked_until=NULL WHERE source_type='campaign' AND source_key=? AND status IN ('pending','failed')",(rid,))
        db.execute("UPDATE campaign_recipients SET status='skipped',reason='campaign_cancelled' WHERE campaign_id=? AND status='planned'",(campaign_id,))
        audit(db,user['username'],'campaign_cancelled',campaign_id)


def dashboard(db,user,csrf):
    stats=db.execute("SELECT count(*) FROM feedback_tickets WHERE department=? AND status='new'",(ROLE_DEPARTMENTS.get(user['role'],''),)).fetchone()[0]
    links=[]
    if user['role']!='support': links += [('/reports','Аналитика и отчёты')]
    if user['role']=='advertising': links += [('/audience','Аудитория')]
    if user['role']=='advertising': links += [('/campaigns','Рассылки'),('/accounts','Сотрудники'),('/audit','Журнал действий'),('/delivery','Доставка')]
    if user['role'] in ROLE_DEPARTMENTS: links += [('/tickets',f'Обращения · новых: {stats}')]
    body=f'<p>{esc(user["username"])} · {esc(ROLES[user["role"]])}</p><ul>'+''.join(
        f'<li><a href="{url}">{esc(label)}</a></li>' for url,label in links)+'</ul>'
    body+='<p><a href="/profile">Мой пароль и оповещения</a></p>'+form('/logout',csrf,'<button>Выйти</button>')
    return body


def report_page(db,user,query):
    require(user,'reports')
    period=report_period(query)
    current=report_rows(db,user,period['from'],period['to'])
    previous=report_rows(db,user,period['previous_from'],period['previous_to'])
    rows=comparison_rows(current,previous)
    choices={'week':'Неделя','previous_week':'Предыдущая неделя','month':'Месяц',
        'previous_month':'Предыдущий месяц','season':'Театральный сезон','previous_season':'Предыдущий сезон','custom':'Произвольный период'}
    options=''.join(f'<option value="{key}"'+(' selected' if key==period['kind'] else '')+'>'+esc(label)+'</option>' for key,label in choices.items())
    body=f"""<h2>{esc(period['title'])}</h2><form method="get" action="/reports">
        <label>Период <select name="period">{options}</select></label>
        <label>Дата внутри недели/месяца/сезона <input type="date" name="date" value="{period['anchor']}" required></label>
        <label>Для произвольного периода: с <input type="date" name="from" value="{period['from']}"></label>
        <label>По <input type="date" name="to" value="{period['requested_to']}"></label><button>Показать</button></form>
        <p>Отчёт: {period['from']} — {period['to']}. Сравнение: {period['previous_from']} — {period['previous_to']}.</p>
        <p>Неделя — понедельник–воскресенье. Театральный сезон — 1 сентября–10 июля включительно.
        В межсезонье показывается последний завершённый сезон.</p>"""
    if period['partial']:
        body+='<p>Период ещё не завершён: учтены дни по сегодня включительно; сравнение — с такой же прошедшей частью предыдущего периода (в пределах его длины).</p>'
    body+='<p>Строки «сейчас» — текущий срез без исторического сравнения. История изменений собирается с установки панели. Отсутствие старых данных не доказывает отсутствие событий. Прочтения, покупки и переходы без источника данных не учитываются.</p>'
    body+=table(['Показатель','Группа','Значение','За период / сейчас','Предыдущий период','Изменение'],rows)
    from urllib.parse import urlencode
    export={'period':period['kind'],'date':period['anchor'],'from':period['from'],'to':period['requested_to']}
    body+=f'<p><a href="/reports.csv?{urlencode(export)}">Скачать CSV</a></p>'
    return body,rows


def campaign_page(db,theatre,user,csrf,enabled):
    require(user,'campaigns')
    options='<option value="all|">Вся разрешённая аудитория</option>'
    from theatre_bot.interest_preferences import genre_options,artist_options
    catalog=[('genre',v['key'],v['label']) for v in genre_options(theatre)]+[('artist',v['key'],v['label']) for v in artist_options(theatre)]
    catalog += [('play',r['preference_key'],r['preference_label']) for r in db.execute("SELECT DISTINCT preference_key,preference_label FROM preferences WHERE preference_type='play'")]
    options+=''.join(f'<option value="{esc(kind+"|"+key)}">{esc(label)}</option>' for kind,key,label in catalog)
    body='<h2>Рассылки</h2><p>Только активные профили с рекламным согласием. Интервал рекламных сообщений — не чаще одного за 7 дней.</p>'
    if not enabled: body+='<p class="ok">Планирование отправки выключено в настройках сервера. Доступны черновики и предпросмотр.</p>'
    body+=form('/campaign/create',csrf,f'''<label>Название <input name="name" maxlength="120" required></label>
        <label>Канал <select name="channel"><option value="vk">VK</option><option value="max">MAX</option><option value="all">VK и MAX</option></select></label>
        <label>Аудитория <select name="target">{options}</select></label><label><input type="checkbox" name="premieres" value="1"> Только интересующиеся премьерами</label>
        <label>Сообщение <textarea name="message" maxlength="2000" required></textarea></label><button>Сохранить черновик и посмотреть</button>''')
    for c in db.execute('SELECT * FROM campaigns ORDER BY id DESC LIMIT 100'):
        body+=f'<section class="box"><h3>№{c["id"]} · {esc(c["name"])}</h3><p>{esc(STATUS_NAMES.get(c["status"],c["status"]))} · {esc(c["scheduled_at"] or "—")}</p><pre style="white-space:pre-wrap">{esc(c["message"])}</pre>'
        if c['status']=='draft':
            body+=form('/campaign/preview',csrf,hidden('id',c['id'])+'<button>Предпросмотр текста и получателей</button>')
        if c['status']=='approved' and enabled:
            body+=form('/campaign/schedule',csrf,hidden('id',c['id'])+'<label>Дата и время по Челябинску <input type="datetime-local" name="scheduled_at" required></label><label><input type="checkbox" name="confirm" value="yes" required> Подтверждаю отправку в указанное время</label><button>Запланировать отправку</button>')
        if c['status'] in ('draft','approved','scheduled'):
            body+=form('/campaign/cancel',csrf,hidden('id',c['id'])+'<button>Отменить кампанию</button>')
        body+='</section>'
    return body


def create_panel_handler(database_path,theatre_path,protector,security,*,campaigns_enabled=False):
    class Handler(BaseHTTPRequestHandler):
        server_version='TheatrePanel'
        sys_version=''
        def log_message(self,*args): pass
        def setup(self):
            super().setup()
            self.connection.settimeout(15)
        def token(self):
            try:
                cookie=SimpleCookie(self.headers.get('Cookie',''))
                return cookie['staff_session'].value if 'staff_session' in cookie else ''
            except CookieError: return ''
        def send(self,status,body,cookie=None,mime='text/html; charset=utf-8'):
            self.send_response(status)
            for key,value in [('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),
                ('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),
                ('Content-Security-Policy',"default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")]:
                self.send_header(key,value)
            if cookie: self.send_header('Set-Cookie',cookie)
            self.end_headers(); self.wfile.write(body)
        def redirect(self,path,cookie=None):
            self.send_response(303); self.send_header('Location',path); self.send_header('Content-Length','0');self.send_header('Cache-Control','no-store')
            if cookie:self.send_header('Set-Cookie',cookie)
            self.end_headers()
        def page(self,title,body):
            self.send(200,_page(title,'<p><a href="/admin">Главная</a> · <a href="/tickets" id="inbox-badge">Обращения</a></p><button type="button" id="sound-toggle">Включить звук обращений</button>'+body).replace(b'</body>', b'<script src="/panel.js" defer></script></body>'))
        def login_page(self,error=False):
            return _page('Вход','<h2>Панель сотрудников</h2>'+('<p>Вход не выполнен. Проверьте данные или повторите позже.</p>' if error else '')+
                '<form method="post" action="/login"><label>Логин <input name="username" maxlength="64" required autocomplete="username"></label><label>Пароль <input name="password" type="password" maxlength="256" required autocomplete="current-password"></label><button>Войти</button></form>')
        def get_db(self):
            db=connect_subscribers(database_path)
            initialize_subscribers(db)
            return db
        def form_data(self):
            if not self.headers.get('Content-Type','').startswith('application/x-www-form-urlencoded'): raise ValueError('Формат запроса недоступен')
            try: size=int(self.headers.get('Content-Length','0'))
            except ValueError: raise ValueError('Некорректный размер запроса')
            if not 0<size<=16384: raise ValueError('Размер запроса недопустим')
            try: values=parse_qs(self.rfile.read(size).decode('utf-8'),keep_blank_values=True,max_num_fields=30)
            except (UnicodeDecodeError,ValueError): raise ValueError('Некорректный запрос')
            if any(len(v)!=1 for v in values.values()): raise ValueError('Повторяющиеся поля')
            return {k:v[0] for k,v in values.items()}
        def do_GET(self):
            parsed=urlparse(self.path);path=parsed.path
            if path=='/login':self.send(200,self.login_page());return
            db=self.get_db()
            try:
                result=security.session(db,self.token())
                if not result:self.redirect('/login');return
                user,csrf=result
                with db: db.execute('UPDATE admin_users SET last_seen_at=? WHERE id=?',(stamp(),user['id']))
                query={k:v[0] for k,v in parse_qs(parsed.query).items()}
                if path=='/panel.js':
                    self.send(200,PANEL_JS.encode(),mime='text/javascript; charset=utf-8');return
                if path=='/inbox-status':
                    import json
                    dep=ROLE_DEPARTMENTS.get(user['role'])
                    count,last=db.execute("""SELECT count(DISTINCT t.id),COALESCE(MAX(m.id),0) FROM feedback_tickets t
                        LEFT JOIN feedback_messages m ON m.ticket_id=t.id AND m.direction='incoming'
                        WHERE t.department=? AND t.status!='closed'""",(dep or '',)).fetchone()
                    self.send(200,json.dumps({'count':count,'last':last}).encode(),mime='application/json');return
                if path in ('/','/admin'): self.page('Панель',dashboard(db,user,csrf));return
                if path in ('/reports','/reports.csv'):
                    body,rows=report_page(db,user,query)
                    with db:audit(db,user['username'],'report_view' if path=='/reports' else 'report_export')
                    if path.endswith('.csv'):
                        buffer=io.StringIO();writer=csv.writer(buffer)
                        writer.writerow(['Показатель','Группа','Значение','За период / сейчас','Предыдущий период','Изменение'])
                        selected=report_period(query)
                        writer.writerow(['Период',selected['from'],selected['to'],'Сравнение',selected['previous_from'],selected['previous_to']])
                        for row in rows:writer.writerow([("'"+str(v)) if str(v).lstrip().startswith(('=','+','-','@')) else v for v in row])
                        self.send(200,('\ufeff'+buffer.getvalue()).encode(),'','text/csv; charset=utf-8')
                    else:self.page('Отчёты',body)
                    return
                if path=='/audience':
                    require(user,'audience')
                    term=query.get('q','').strip()
                    page=max(0,min(100000,int(query.get('page','0'))))
                    marketing=query.get('marketing')=='yes'
                    rows=db.execute("""SELECT s.* FROM subscribers s WHERE status='active'
                        AND (?='' OR EXISTS(SELECT 1 FROM preferences p WHERE p.subscriber_id=s.id AND p.preference_label LIKE ?))
                        AND (?=0 OR (SELECT action FROM consent_events c WHERE c.subscriber_id=s.id AND consent_type='marketing' ORDER BY id DESC LIMIT 1)='granted')
                        ORDER BY s.id LIMIT 100 OFFSET ?""",(term,'%'+term+'%',int(marketing),page*100)).fetchall()
                    profiles=tuple(_build_profile(db,protector,r) for r in rows)
                    with db:
                        db.execute("INSERT INTO subscriber_access_log(subscriber_id,admin_actor,action,purpose,accessed_at) VALUES(NULL,?,'list_profiles','staff_panel',?)",(user['username'],stamp()))
                    self.page('Аудитория','<h2>Аудитория</h2><form method="get" action="/audience"><input name="q" placeholder="Жанр или артист"><label><input type="checkbox" name="marketing" value="yes"> Только с рекламным согласием</label><button>Показать</button></form>'+table(['Имя','Канал','ID','Интересы','Премьеры','Рекламное согласие'],
                        [(p.display_name,p.channel,p.external_id,', '.join(v for _,v in p.preferences),'да' if p.interested_in_premieres else 'нет','да' if p.consents['marketing'] else 'нет') for p in profiles])+f'<p>До 100 профилей на странице. <a href="/audience?page={page+1}">Следующая страница</a></p>');return
                if path=='/campaigns':
                    require(user,'campaigns')
                    theatre=connect(theatre_path)
                    try:body=campaign_page(db,theatre,user,csrf,campaigns_enabled)
                    finally:theatre.close()
                    self.page('Рассылки',body);return
                if path=='/tickets':
                    require(user,'tickets');dep=ROLE_DEPARTMENTS[user['role']]
                    body=f'<h2>Обращения · {esc(DEPARTMENTS[dep])}</h2><p>Обновляйте список, чтобы видеть новые обращения. Оповещения вне панели настраиваются в профиле.</p>'
                    for t in db.execute('''SELECT t.*,s.channel,u.username FROM feedback_tickets t
                        JOIN subscribers s ON s.id=t.subscriber_id LEFT JOIN admin_users u ON u.id=t.assigned_to
                        WHERE t.department=? ORDER BY CASE t.status WHEN 'new' THEN 0 WHEN 'working' THEN 1 ELSE 2 END,t.updated_at DESC LIMIT 200''',(dep,)):
                        body+=f'<p><a href="/ticket?id={t["id"]}">№{t["id"]}</a> · {esc(t["channel"])} · {esc(STATUS_NAMES.get(t["status"],t["status"]))} · {esc(t["username"] or "не назначен")} · {esc(t["updated_at"])}</p>'
                    with db:
                        cursor=db.execute("SELECT COALESCE(MAX(m.id),0) FROM feedback_alert_events m JOIN feedback_tickets t ON t.id=m.ticket_id WHERE t.department=?",(dep,)).fetchone()[0]
                        db.execute('UPDATE admin_users SET alert_cursor=MAX(alert_cursor,?) WHERE id=?',(cursor,user['id']))
                    self.page('Обращения',body);return
                if path=='/ticket':
                    tid=int(query.get('id','0'));ticket=authorized_ticket(db,user,tid)
                    body=f'<h2>Обращение №{tid} · {esc(DEPARTMENTS[ticket["department"]])}</h2><p>Статус: {esc(STATUS_NAMES.get(ticket["status"],ticket["status"]))}</p>'
                    for m in db.execute('SELECT m.*,u.username,q.status AS delivery_status,q.failure_reason AS delivery_error FROM feedback_messages m LEFT JOIN admin_users u ON u.id=m.staff_id LEFT JOIN outgoing_reply_queue q ON q.id=m.outgoing_queue_id WHERE ticket_id=? ORDER BY m.id',(tid,)):
                        body+=f'<section class="box"><b>{esc(m["username"] or "Зритель")}</b> · {esc(m["created_at"])}<pre style="white-space:pre-wrap">{esc(protector.decrypt(m["body_encrypted"]) if m["body_encrypted"] else "Содержимое удалено по сроку хранения")}</pre></section>'
                        if m['direction']=='outgoing':
                            body+='<p>Доставка: '+esc(STATUS_NAMES.get(m['delivery_status'],m['delivery_status'] or 'неизвестно'))+(' · требуется проверка результата' if m['delivery_error']=='delivery_uncertain' else '')+'</p>'
                    if ticket['status']!='closed':
                        body+=form('/ticket/reply',csrf,hidden('id',tid)+hidden('request_token',secrets.token_urlsafe(24))+'<textarea name="message" maxlength="2000" required></textarea><button>Отправить ответ</button>')
                        for action,label in [('take','Взять в работу'),('close','Закрыть обращение')]:
                            body+=form('/ticket/'+action,csrf,hidden('id',tid)+f'<button>{label}</button>')
                        body+=form('/ticket/transfer',csrf,hidden('id',tid)+'<select name="department">'+''.join(f'<option value="{d}">{esc(label)}</option>' for d,label in DEPARTMENTS.items())+'</select><button>Перенаправить</button>')
                    with db:audit(db,user['username'],'ticket_view',tid)
                    self.page('Обращение',body);return
                if path=='/profile':
                    body='<h2>Мой профиль</h2>'+form('/profile/password',csrf,'<label>Текущий пароль <input type="password" name="old" required></label><label>Новый пароль (от 12 символов) <input type="password" name="new" minlength="12" maxlength="256" required></label><button>Изменить пароль</button>')
                    if user['role'] in ROLE_DEPARTMENTS:
                        body+='<h3>Оповещения вне панели</h3><p>Получите код и отправьте указанную команду боту из своего рабочего VK или MAX. Вводить числовой ID не нужно.</p>'+form('/profile/notify',csrf,'<button>Получить код подключения</button>')
                        body+=form('/profile/notify-off',csrf,'<button>Отключить оповещения</button>')
                        body+='<p>Оповещения: '+('включены' if user['notify_id_encrypted'] else 'не настроены')+'</p>'
                    self.page('Мой профиль',body);return
                if path=='/accounts':
                    require(user,'accounts')
                    body='<h2>Сотрудники</h2>'+form('/account/create',csrf,'<input name="username" maxlength="64" required placeholder="Логин"><input type="password" name="password" minlength="12" maxlength="256" required placeholder="Начальный пароль"><select name="role">'+''.join(f'<option value="{r}">{esc(label)}</option>' for r,label in ROLES.items())+'</select><button>Создать учётную запись</button>')
                    for u in db.execute('SELECT id,username,role,active FROM admin_users ORDER BY id'):
                        body+=f'<p>{esc(u["username"])} · {esc(ROLES[u["role"]])} · {"активен" if u["active"] else "отключён"}</p>'
                        if u['id']!=user['id']:
                            body+=form('/account/active',csrf,hidden('id',u['id'])+hidden('active',0 if u['active'] else 1)+'<button>Изменить доступ</button>')
                            body+=form('/account/role',csrf,hidden('id',u['id'])+'<select name="role">'+''.join(f'<option value="{r}"'+(' selected' if r==u['role'] else '')+'>'+esc(label)+'</option>' for r,label in ROLES.items())+'</select><button>Изменить роль</button>')
                    self.page('Сотрудники',body);return
                if path=='/audit':
                    require(user,'accounts');rows=db.execute('SELECT actor,action,object_key,recorded_at FROM admin_audit ORDER BY id DESC LIMIT 200').fetchall()
                    self.page('Журнал',table(['Сотрудник','Действие','Объект','Время'],rows));return
                if path=='/delivery':
                    require(user,'campaigns');rows=db.execute('SELECT channel,status,failure_reason,count(*) FROM outgoing_reply_queue GROUP BY channel,status,failure_reason').fetchall()
                    self.page('Доставка',table(['Канал','Статус','Причина','Количество'],rows)+'<p>delivery_uncertain требует проверки сотрудником; автоматически повторять такую отправку нельзя.</p>');return
                self.send(404,_page('Не найдено','Страница не найдена'))
            except PermissionError:self.send(403,_page('Доступ запрещён','Недостаточно прав.'))
            except (ValueError,LookupError):self.send(400,_page('Ошибка','Проверьте параметры запроса.'))
            except Exception:self.send(500,_page('Ошибка','Не удалось открыть страницу.'))
            finally:db.close()
        def do_POST(self):
            db=None
            try:
                values=self.form_data();path=urlparse(self.path).path
                db=self.get_db()
                origin=self.headers.get('Origin')
                if origin and urlparse(origin).netloc!=self.headers.get('Host'):raise PermissionError()
                if path=='/login':
                    token=security.login(db,values.get('username',''),values.get('password',''))
                    if not token:self.send(401,self.login_page(True));return
                    self.redirect('/admin',f'staff_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=1800');return
                result=security.session(db,self.token())
                if not result:raise PermissionError()
                user,csrf=result
                if not secrets.compare_digest(csrf,values.get('csrf','')):raise PermissionError()
                origin=self.headers.get('Origin')
                if origin and urlparse(origin).netloc!=self.headers.get('Host'):raise PermissionError()
                if path=='/logout':
                    security.logout(self.token());self.redirect('/login','staff_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0');return
                if path.startswith('/campaign/'):
                    require(user,'campaigns');action=path.split('/')[-1]
                    if action=='create':
                        kind,key=values.get('target','').split('|',1)
                        if kind!='all':
                            theatre=connect(theatre_path)
                            try:
                                from theatre_bot.interest_preferences import genre_options,artist_options
                                if kind=='genre':catalog={v['key']:v['label'] for v in genre_options(theatre)}
                                elif kind=='artist':catalog={v['key']:v['label'] for v in artist_options(theatre)}
                                elif kind=='play':catalog={r['preference_key']:r['preference_label'] for r in db.execute("SELECT preference_key,preference_label FROM preferences WHERE preference_type='play'")}
                                else:raise ValueError()
                                label=catalog[key]
                            finally:theatre.close()
                        else:label='Вся аудитория'
                        cid=create_campaign(db,values.get('name','')[:120],values.get('message',''),user['username'],channel=values.get('channel','vk'),target_type=kind,target_key=key or None,target_label=label,premieres_only=values.get('premieres')=='1')
                        action='preview'
                    else:cid=int(values.get('id','0'))
                    if action=='preview':
                        preview=preview_campaign(db,protector,cid,user['username'],'staff_panel')
                        c=db.execute('SELECT * FROM campaigns WHERE id=?',(cid,)).fetchone()
                        body=f'<h2>{esc(c["name"])}</h2><pre style="white-space:pre-wrap">{esc(c["message"])}</pre><p>Получателей сейчас: {preview.total}. Аудитория пересчитывается перед отправкой.</p>'
                        body+=table(['Канал','Получателей'],preview.by_channel.items())+form('/campaign/approve',csrf,hidden('id',cid)+'<label><input type="checkbox" name="confirm" value="yes" required> Текст и аудитория проверены</label><button>Подтвердить кампанию</button>')
                        self.page('Предпросмотр',body);return
                    if action=='approve':
                        if values.get('confirm')!='yes':raise ValueError('Подтвердите проверку текста и аудитории')
                        approve_campaign(db,cid,user['username'])
                    elif action=='schedule':
                        if not campaigns_enabled:raise ValueError('Планирование выключено на сервере')
                        if values.get('confirm')!='yes':raise ValueError('Нужно подтверждение отправки')
                        schedule_campaign(db,cid,parse_scheduled_at(values.get('scheduled_at','')),user['username'])
                    elif action=='cancel':cancel_campaign(db,user,cid)
                    else:raise ValueError()
                    with db:audit(db,user['username'],'campaign_'+action,cid)
                    self.redirect('/campaigns');return
                if path.startswith('/ticket/'):
                    tid=int(values.get('id','0'));action=path.split('/')[-1]
                    if action=='reply':staff_reply(db,protector,user,tid,values.get('message',''),values.get('request_token',''))
                    else:change_ticket(db,user,tid,action,values.get('department'))
                    self.redirect('/tickets' if action=='transfer' else f'/ticket?id={tid}');return
                if path=='/account/create':
                    require(user,'accounts');create_staff(db,values.get('username',''),values.get('password',''),values.get('role',''),user['username'])
                    self.redirect('/accounts');return
                if path=='/account/active':
                    set_staff_active(db,user,int(values.get('id','0')),values.get('active')=='1');self.redirect('/accounts');return
                if path=='/account/role':
                    set_staff_role(db,user,int(values.get('id','0')),values.get('role',''));self.redirect('/accounts');return
                if path=='/profile/password':
                    if not password_matches(values.get('old',''),user['password_hash']):raise ValueError('Текущий пароль неверен')
                    new=password_hash(values.get('new',''))
                    with db:
                        db.execute('UPDATE admin_users SET password_hash=? WHERE id=?',(new,user['id']));audit(db,user['username'],'password_changed')
                    security.logout(self.token());self.redirect('/login');return
                if path in ('/profile/notify','/profile/notify-off'):
                    require(user,'tickets')
                    command=self.notification_action(db,user,values,path)
                    if command:
                        self.page('Подключение оповещений','<h2>Подключение оповещений</h2><p>Отправьте эту команду боту из своего рабочего чата VK или MAX. Код действует 10 минут, не передавайте его другим людям.</p><pre>'+esc(command)+'</pre><p><a href="/profile">Вернуться в профиль</a></p>')
                    else:self.redirect('/profile')
                    return
                self.send(404,_page('Ошибка','Действие не найдено'))
            except PermissionError:self.send(403,_page('Доступ запрещён','Недостаточно прав или сессия завершена.'))
            except (ValueError,LookupError,KeyError,sqlite3.IntegrityError) as error:
                self.send(400,_page('Ошибка','Не удалось выполнить действие. Проверьте поля, состояние объекта и права доступа.'))
            except Exception:self.send(500,_page('Ошибка','Не удалось выполнить действие.'))
            finally:
                if db is not None:db.close()
        def notification_action(self,db,user,values,path):
            from theatre_bot.persistent_replies import enqueue_reply
            from theatre_bot.channels import OutgoingMessage
            from theatre_bot.dialog import Reply
            if path=='/profile/notify-off':
                with db:
                    db.execute('UPDATE admin_users SET notify_channel=NULL,notify_id_encrypted=NULL WHERE id=?',(user['id'],))
                    db.execute('DELETE FROM staff_notify_challenges WHERE staff_id=?',(user['id'],));audit(db,user['username'],'notifications_disabled')
                return
            if path=='/profile/notify':
                code=secrets.token_urlsafe(24)
                with db:
                    db.execute("""INSERT OR REPLACE INTO staff_notify_challenges
                        (staff_id,code_hash,created_at,expires_at,attempts) VALUES(?,?,?,?,0)""",
                        (user['id'],hashlib.sha256(code.encode()).hexdigest(),stamp(),stamp(datetime.now(timezone.utc)+timedelta(minutes=10))))
                    audit(db,user['username'],'notification_confirmation_requested')
                return 'Подключить уведомления '+code
    return Handler


def run_panel(database_path,theatre_path,protector,port=8090,campaigns_enabled=False):
    if not 1024<=port<=65535:raise ValueError('Некорректный порт')
    db=connect_subscribers(database_path)
    try:
        initialize_subscribers(db)
        if not db.execute("SELECT 1 FROM admin_users WHERE active=1 AND role='advertising'").fetchone():
            raise ValueError('Сначала создайте администратора командой manage_staff.py')
    finally:db.close()
    server=ThreadingHTTPServer(('127.0.0.1',port),create_panel_handler(database_path,theatre_path,protector,PanelSecurity(),campaigns_enabled=campaigns_enabled))
    server.daemon_threads=True
    print(f'Панель: http://127.0.0.1:{port}/admin. Доступ только через локальный компьютер/SSH-туннель.')
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


PANEL_JS = r"""
(() => {
  let last = null, audio = null, enabled = false;
  const toggle = document.getElementById('sound-toggle');
  const badge = document.getElementById('inbox-badge');
  if (!toggle || !badge) return;
  toggle.onclick = () => {
    enabled = !enabled;
    toggle.textContent = enabled ? 'Звук включён' : 'Включить звук обращений';
    if (enabled) { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); }
  };
  async function poll() {
    if (document.hidden) return;
    try {
      const response = await fetch('/inbox-status', {cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      badge.textContent = `Обращения · открыто: ${data.count}`;
      if (last !== null && data.last > last) {
        badge.textContent += ' · новое сообщение';
        if (enabled && audio) {
          const tone = audio.createOscillator(), gain = audio.createGain();
          tone.connect(gain); gain.connect(audio.destination); gain.gain.value=0.08;
          tone.start(); tone.stop(audio.currentTime+0.15);
        }
        if (location.pathname==='/tickets') location.reload();
      }
      last = data.last;
    } catch (_) { /* A later poll retries; no dialogue content is logged. */ }
  }
  poll(); setInterval(poll, 20000);
})();
"""
