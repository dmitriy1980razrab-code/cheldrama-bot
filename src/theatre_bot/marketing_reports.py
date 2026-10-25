"""Aggregate marketing reports; history starts when change recording is installed."""
from datetime import datetime, timedelta, timezone
from theatre_bot.admin_access import require


def record_preference_changes(db,subscriber_id,before,after,at):
    local=datetime.fromisoformat(at).astimezone(timezone(timedelta(hours=5)))
    bucket=local.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).isoformat(timespec='seconds')
    for action,items in [('removed',before-after),('added',after-before)]:
        for kind,label in sorted(items):
            db.execute('''INSERT INTO preference_changes
                (preference_type,preference_label,action,recorded_at) VALUES(?,?,?,?)''',
                (kind,label,action,bucket))


def preference_snapshot(db,subscriber_id):
    values={(r['preference_type'],r['preference_label']) for r in db.execute(
        "SELECT preference_type,preference_label FROM preferences WHERE subscriber_id=? AND source='interest_survey'",(subscriber_id,))}
    row=db.execute('SELECT interested_in_premieres FROM subscribers WHERE id=?',(subscriber_id,)).fetchone()
    if row and row[0]: values.add(('premiere','Премьеры'))
    return values


def report_rows(db,user,start,end):
    require(user,'reports')
    try:
        first=datetime.strptime(start,'%Y-%m-%d').replace(tzinfo=timezone(timedelta(hours=5)))
        last=datetime.strptime(end,'%Y-%m-%d').replace(tzinfo=first.tzinfo)+timedelta(days=1)
    except (ValueError,TypeError): raise ValueError('Укажите даты отчёта')
    if first>=last or (last-first).days>366: raise ValueError('Период — от 1 до 366 дней')
    bounds=(first.astimezone(timezone.utc).isoformat(),last.astimezone(timezone.utc).isoformat())
    result=[]
    def add(section,sql,args=()):
        for row in db.execute(sql,args): result.append((section,*tuple(row)))
    add('Профили сейчас',"SELECT channel,status,count(*) FROM subscribers GROUP BY channel,status")
    add('Рекламное согласие сейчас',"""SELECT channel,'действует',count(*) FROM subscribers s WHERE status='active' AND
        (SELECT action FROM consent_events c WHERE c.subscriber_id=s.id AND consent_type='marketing' ORDER BY id DESC LIMIT 1)='granted'
        GROUP BY channel""")
    add('Приоритеты сейчас',"SELECT preference_type,preference_label,count(*) FROM preferences WHERE source='interest_survey' GROUP BY preference_type,preference_key,preference_label ORDER BY count(*) DESC")
    add('Премьеры сейчас',"SELECT 'premiere','Премьеры',count(*) FROM subscribers WHERE interested_in_premieres=1")
    add('Изменения предпочтений за период',"SELECT action,preference_label,count(*) FROM preference_changes WHERE recorded_at>=? AND recorded_at<? GROUP BY action,preference_type,preference_label",bounds)
    add('Согласия за период',"SELECT consent_type,action,count(*) FROM consent_events WHERE recorded_at>=? AND recorded_at<? GROUP BY consent_type,action",bounds)
    add('Подписки на показы за период',"SELECT 'подписка',topic_label,count(*) FROM subscriptions WHERE subscribed_at>=? AND subscribed_at<? GROUP BY topic_key,topic_label",bounds)
    add('Отписки от показов за период',"SELECT 'отписка',topic_label,count(*) FROM subscriptions WHERE unsubscribed_at>=? AND unsubscribed_at<? GROUP BY topic_key,topic_label",bounds)
    add('Результаты кампаний за период',"SELECT 'получатели',cr.status,count(*) FROM campaign_recipients cr JOIN campaigns c ON c.id=cr.campaign_id WHERE COALESCE(cr.sent_at,c.scheduled_at,c.created_at)>=? AND COALESCE(cr.sent_at,c.scheduled_at,c.created_at)<? GROUP BY cr.status",bounds)
    add('Отзыв рекламы в течение 7 дней после кампании (не доказывает причину)',"""SELECT c.name,'профилей',count(DISTINCT cr.subscriber_id)
        FROM campaign_recipients cr JOIN campaigns c ON c.id=cr.campaign_id JOIN consent_events ce ON ce.subscriber_id=cr.subscriber_id
        WHERE cr.status='sent' AND cr.sent_at>=? AND cr.sent_at<? AND ce.consent_type='marketing' AND ce.action='revoked'
          AND julianday(ce.recorded_at)>=julianday(cr.sent_at) AND julianday(ce.recorded_at)<=julianday(cr.sent_at)+7
        GROUP BY c.id,c.name""",bounds)
    # No text or identity from other departments is exposed in aggregate reports.
    add('Обращения за период',"SELECT department,status,count(*) FROM feedback_tickets WHERE created_at>=? AND created_at<? GROUP BY department,status",bounds)
    add('Первый ответ, минуты (среднее)',"""SELECT department,'минуты',ROUND(AVG(delay),1) FROM (
        SELECT t.department,(julianday((SELECT min(m.created_at) FROM feedback_messages m WHERE m.ticket_id=t.id AND direction='outgoing'))-julianday(t.created_at))*1440 AS delay
        FROM feedback_tickets t WHERE t.created_at>=? AND t.created_at<?) WHERE delay IS NOT NULL GROUP BY department""",bounds)
    names={'genre':'Жанр','artist':'Артист','premiere':'Премьеры','added':'Добавлено','removed':'Удалено',
        'personal_data':'Согласие на ПДн','marketing':'Реклама','service_notifications':'Сервисные уведомления',
        'granted':'Выдано','revoked':'Отозвано','advertising':'Реклама, акции','social':'Соц.сети','technical':'Техподдержка',
        'new':'Новое','working':'В работе','closed':'Закрыто','active':'Активен','pending':'Ожидает',
        'unsubscribed':'Отписан','sent':'Отправлено','failed':'Ошибка','skipped':'Пропущено','planned':'Запланировано'}
    return [tuple(names.get(v,v) if isinstance(v,str) else v for v in row) for row in result]


def report_period(query,today=None):
    """Calendar periods in theatre time; July 10 is included in the season."""
    from datetime import date
    import calendar
    today=today or datetime.now(timezone(timedelta(hours=5))).date()
    kind=query.get('period') or ('custom' if query.get('from') else 'month')
    try: anchor=date.fromisoformat(query.get('date') or today.isoformat())
    except ValueError:raise ValueError('Проверьте дату')
    if kind in ('week','previous_week'):
        first=anchor-timedelta(days=anchor.weekday())
        if kind=='previous_week':first-=timedelta(days=7)
        last=first+timedelta(days=6);previous_first=first-timedelta(days=7);previous_last=last-timedelta(days=7)
        title='Недельный отчёт'
    elif kind in ('month','previous_month'):
        first=anchor.replace(day=1)
        if kind=='previous_month':first=(first-timedelta(days=1)).replace(day=1)
        last=first.replace(day=calendar.monthrange(first.year,first.month)[1])
        previous_last=first-timedelta(days=1);previous_first=previous_last.replace(day=1)
        title='Месячный отчёт'
    elif kind in ('season','previous_season'):
        year=anchor.year if anchor.month>=9 else anchor.year-1
        if kind=='previous_season':year-=1
        first=date(year,9,1);last=date(year+1,7,10)
        previous_first=date(year-1,9,1);previous_last=date(year,7,10)
        title=f'Театральный сезон {year}/{year+1}'
    elif kind=='custom':
        try:first=date.fromisoformat(query.get('from',''));last=date.fromisoformat(query.get('to',''))
        except ValueError:raise ValueError('Укажите начало и конец периода')
        if first>last or (last-first).days>=366:raise ValueError('Период — от 1 до 366 дней')
        previous_last=first-timedelta(days=1);previous_first=previous_last-(last-first)
        title='Отчёт за выбранный период'
    else:raise ValueError('Неизвестный период отчёта')
    requested_last=last
    partial=first<=today<last
    if partial:
        last=today
        previous_last=min(previous_last,previous_first+(last-first))
    return {'kind':kind,'anchor':anchor.isoformat(),'title':title,'from':first.isoformat(),'to':last.isoformat(),
            'requested_to':requested_last.isoformat(),'previous_from':previous_first.isoformat(),
            'previous_to':previous_last.isoformat(),'partial':partial}


def comparison_rows(current,previous):
    # A current snapshot cannot stand in for a historical snapshot.
    now={tuple(r[:3]):r[3] for r in current}
    old={tuple(r[:3]):r[3] for r in previous if not r[0].endswith('сейчас')}
    result=[]
    for key in sorted(set(now)|set(old)):
        value=now.get(key,0)
        if key[0].endswith('сейчас'):
            result.append((*key,value,'—','—'))
        else:
            before=old.get(key,0)
            result.append((*key,value,before,round(value-before,2)))
    return result
