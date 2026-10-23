"""Optional VK/MAX interest questionnaire, with expiring encrypted drafts."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import calendar
import secrets

from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.notifications import THEATRE_TIMEZONE
from theatre_bot.interest_preferences import (genre_options, artist_options, current_preferences,
    personal_data_granted, save_preferences, delete_preferences)
from theatre_bot.spam_protection import normalize_message
from theatre_bot.subscribers import record_consent, _timestamp
from theatre_bot.subscription_flow import DOCUMENT_VERSIONS, legal_documents_reply

OPEN_COMMANDS = {'анкета интересов', 'мои интересы', 'интересы', 'изменить интересы'}
PAGE_SIZE = 3


def next_month(moment):
    year = moment.year + (moment.month == 12)
    month = moment.month % 12 + 1
    return moment.replace(year=year, month=month,
                          day=min(moment.day, calendar.monthrange(year, month)[1]))


class InterestSurvey:
    def __init__(self, theatre, subscribers, protector):
        self.theatre, self.db, self.protector = theatre, subscribers, protector

    def _actor(self, message):
        return self.protector.lookup_hash(message.channel, 'interest-survey:' + message.external_user_id)

    def _now(self, now):
        now = now or datetime.now(timezone.utc)
        return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)

    def _load(self, message, now):
        with self.db:
            self.db.execute("UPDATE interest_survey_state SET stage='expired', payload_encrypted=NULL WHERE expires_at<=? AND payload_encrypted IS NOT NULL", (_timestamp(now),))
        row = self.db.execute('SELECT * FROM interest_survey_state WHERE actor_hash=?', (self._actor(message),)).fetchone()
        if row is None or row['payload_encrypted'] is None:
            return None
        return row['stage'], json.loads(self.protector.decrypt(row['payload_encrypted']))

    def _save(self, message, stage, data, now):
        data['token'] = secrets.token_urlsafe(12)
        expires = self._now(now)+timedelta(minutes=30)
        with self.db:
            self.db.execute('''INSERT INTO interest_survey_state
                (actor_hash, stage, payload_encrypted, expires_at, updated_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(actor_hash) DO UPDATE SET stage=excluded.stage,
                payload_encrypted=excluded.payload_encrypted, expires_at=excluded.expires_at, updated_at=excluded.updated_at''',
                (self._actor(message), stage, self.protector.encrypt(json.dumps(data, ensure_ascii=False)),
                 _timestamp(expires), _timestamp(now)))

    def close(self, message, now=None):
        with self.db:
            self.db.execute("UPDATE interest_survey_state SET stage='closed', payload_encrypted=NULL, next_offer_at=NULL, updated_at=? WHERE actor_hash=?", (_timestamp(now), self._actor(message)))

    def valid_cancel(self, message, now=None):
        state = self._load(message, now)
        return bool(state and (
            message.action == f"survey:{state[1]['token']}:cancel"
            or (state[0] in {'intro','sections'} and message.action == f"survey:{state[1]['token']}:delete")
            or (state[0]=='delete_confirm' and message.action == f"survey:{state[1]['token']}:delete_yes")
            or (not message.action and normalize_message(message.text)=='отмена' and state[0]!='offered')
        ))

    def _reply(self, message, data, text, buttons):
        return OutgoingMessage(message.channel, message.external_user_id, Reply(text),
            tuple(OutgoingButton(f"survey:{data['token']}:{action}", label) for action, label in buttons))

    def _menu_reply(self, message, text):
        return OutgoingMessage(message.channel, message.external_user_id, Reply(text),
            (OutgoingButton('survey:open', 'Анкета интересов'),), keyboard_inline=message.channel!='vk')

    def decline(self, message, now=None):
        self.close(message, now)
        with self.db:
            self.db.execute("""UPDATE subscribers SET external_id_encrypted=NULL, display_name_encrypted=NULL
                WHERE channel=? AND external_id_hash=? AND status='pending'
                  AND NOT EXISTS(SELECT 1 FROM preferences p WHERE p.subscriber_id=subscribers.id)
                  AND NOT EXISTS(SELECT 1 FROM subscriptions s WHERE s.subscriber_id=subscribers.id AND s.status='active')""",
                (message.channel, self.protector.lookup_hash(message.channel, message.external_user_id)))
        return self._menu_reply(message, 'Новые ответы не сохранены. Прежние настройки не изменены.')

    def offer(self, message, outgoing, now=None):
        # Manual entry only. Do not create drafts or repeat invitations.
        if (not message.action and not outgoing.buttons
                and not outgoing.reply.choices
                and not outgoing.reply.text.startswith(('Слишком много сообщений', 'Сообщение слишком длинное'))):
            return replace(outgoing, buttons=(OutgoingButton('survey:open', 'Анкета интересов'),), keyboard_inline=message.channel!='vk')
        return outgoing

    def start(self, message, now=None):
        data = {}
        self._save(message, 'intro', data, now)
        return self._reply(message, data,
            'Выберите жанры, любимых артистов и интерес к премьерам, чтобы театр мог предлагать подходящие спектакли, акции и приглашения. '
            'Анкета добровольная, вопросы можно пропустить, ответы — изменить или удалить. Разрешение на рекламу запрашивается отдельно.',
            [('begin','Начать'),('delete','Удалить данные анкеты'),('legal','Правовые документы'),('cancel','Пропустить')])

    def _begin_choices(self, message, now):
        data = current_preferences(self.db, self.protector, message.channel, message.external_user_id)
        data.update(genre_options=genre_options(self.theatre), artist_options=artist_options(self.theatre), page=0, search='')
        return self._sections(message, data, now)

    def _sections(self, message, data, now):
        self._save(message, 'sections', data, now)
        genres = ', '.join(v['label'] for v in data['genre_options'] if v['key'] in data['genres']) or 'не выбраны'
        artists = ', '.join(v['label'] for v in data['artist_options'] if v['key'] in data['artists']) or 'не выбраны'
        premieres = 'да' if data['premieres'] else 'нет'
        return self._reply(message, data,
            f'Анкета интересов. Выберите раздел, который хотите заполнить или изменить.\n'
            f'Жанры: {genres}.\nАртисты: {artists}.\nПремьеры: {premieres}.\n'
            'Любой раздел можно оставить без выбора. Для выхода напишите «Отмена».',
            [('genres','Жанры'),('artists','Артисты'),('premieres','Премьеры'),('finish','Завершить анкету'),('delete','Удалить данные анкеты')])

    def _premieres(self, message, data, now):
        self._save(message, 'premieres', data, now)
        return self._reply(message, data, 'Интересуют ли Вас премьеры театра?',
            [('yes','Да'),('no','Нет'),('skip','Назад к разделам')])

    def _finish(self, message, data, now):
        self._save(message, 'marketing', data, now)
        return self._reply(message, data,
            'Нажимая «Сохранить», Вы сохраняете интересы и соглашаетесь получать от театра рекламные предложения, акции и приглашения. '
            'Разрешение можно отозвать командой «Отписаться». При выборе «Не сохранять» новые ответы будут удалены; прежние настройки останутся без изменений.',
            [('allow','Сохранить'),('decline','Не сохранять'),('sections','Изменить ответы')])

    def _choices(self, message, stage, data, now):
        options = data['genre_options' if stage=='genres' else 'artist_options']
        if stage=='artists' and data.get('search'):
            options = [v for v in options if data['search'] in normalize_message(v['label']).replace('ё','е')]
        page_count = max(1, (len(options)+PAGE_SIZE-1)//PAGE_SIZE)
        page = data.get('page', 0)%page_count
        data['page'] = page
        visible = options[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
        data['visible'] = visible
        self._save(message, stage, data, now)
        selected = set(data[stage])
        buttons = [(f'toggle:{i}', ('✓ ' if v['key'] in selected else '')+v['label'][:35]) for i, v in enumerate(visible)]
        if page_count>1:
            buttons.append(('page', 'Ещё жанры' if stage=='genres' else 'Ещё артисты'))
        buttons.append(('clear', 'Снять все отметки'))
        buttons.append(('next', 'К разделам анкеты'))
        heading = 'Какие жанры Вам интересны?' if stage=='genres' else 'Кого из артистов Вы хотели бы видеть на сцене?\nМожно ввести фамилию для поиска; «Все артисты» — вернуть список.'
        all_options = data['genre_options' if stage=='genres' else 'artist_options']
        labels = [v['label'] for v in all_options if v['key'] in selected]
        summary = ', '.join(labels) if labels else 'пока ничего'
        text = heading+f'\nВыбрано: {summary}.\nМожно выбрать несколько вариантов. Повторное нажатие снимает выбор.\nДля выхода напишите «Отмена».'
        if not options:
            text += '\nПодходящих вариантов нет. Можно пропустить вопрос.'
        return self._reply(message, data, text, buttons)

    def try_handle(self, message, now=None):
        text = normalize_message(message.text)
        if (not message.action and text in OPEN_COMMANDS) or message.action=='survey:open':
            return self.start(message, now)
        state = self._load(message, now)
        if not message.action:
            if not state or state[0]=='offered':
                return None
            stage, data = state
            if text=='отмена':
                return self.decline(message, now)
            if stage=='artists':
                data['search'] = '' if text=='все артисты' else text.replace('ё','е')
                data['page'] = 0
                return self._choices(message, stage, data, now)
            self.close(message, now)
            return None
        if not message.action.startswith('survey:'):
            return None
        if not state or not message.action.startswith(f"survey:{state[1]['token']}:"):
            return OutgoingMessage(message.channel, message.external_user_id,
                Reply('Эта кнопка анкеты уже неактуальна. Напишите «Анкета интересов», чтобы открыть её заново.'))
        stage, data = state
        action = message.action.split(':', 2)[2]
        if action=='cancel':
            return self.decline(message, now)
        if action=='delete' and stage in {'intro','sections'}:
            self._save(message, 'delete_confirm', {}, now)
            _, confirmation = self._load(message, now)
            return self._reply(message, confirmation,
                'Удалить все данные анкеты: жанры, артистов и интерес к премьерам? '
                'Согласие на рекламные сообщения будет отозвано. Подписки на показы сохранятся.',
                [('delete_yes','Да, удалить'),('delete_back','Не удалять')])
        if stage=='delete_confirm' and action=='delete_back':
            return self.start(message, now)
        if stage=='delete_confirm' and action=='delete_yes':
            try:
                delete_preferences(self.db, self.protector, message.channel, message.external_user_id,
                    DOCUMENT_VERSIONS['marketing'], self._actor(message), data['token'], now=now)
            except LookupError:
                return self._menu_reply(message, 'Кнопка устарела. Откройте анкету заново.')
            return self._menu_reply(message,
                'Данные анкеты удалены. Согласие на рекламные сообщения отозвано. Подписки на показы сохранены.')
        if stage=='decline_options' and action in {'never','later'}:
            self.close(message, now)
            return self._menu_reply(message, 'Автоматические предложения анкеты отключены. Открыть её можно самостоятельно.')
        if action=='legal' and stage in {'intro','consent'}:
            return self._reply(message, data, legal_documents_reply().text, [('back','Назад'),('cancel','Отмена')])
        if action=='back' and stage in {'intro','consent'}:
            return self.start(message, now) if stage=='intro' else self._consent(message, data, now)
        if stage=='offered' and action=='open':
            return self.start(message, now)
        if stage=='intro' and action=='begin':
            if personal_data_granted(self.db, self.protector, message.channel, message.external_user_id):
                return self._begin_choices(message, now)
            return self._consent(message, data, now)
        if stage=='consent' and action=='accept':
            record_consent(self.db, self.protector, message.channel, message.external_user_id,
                'personal_data', DOCUMENT_VERSIONS['personal_data'], True, 'interest_survey', at=now, display_name=message.display_name)
            return self._begin_choices(message, now)
        if stage=='sections' and action in {'genres','artists','premieres','finish'}:
            data['page'] = 0
            data['search'] = ''
            if action in {'genres','artists'}:
                return self._choices(message, action, data, now)
            if action=='premieres':
                return self._premieres(message, data, now)
            return self._finish(message, data, now)
        if stage=='marketing' and action=='sections':
            return self._sections(message, data, now)
        if stage in {'genres','artists'}:
            if action=='clear':
                data[stage] = []
                return self._choices(message, stage, data, now)
            if action.startswith('toggle:'):
                try:
                    index = int(action.split(':')[1])
                    if index<0:
                        raise ValueError
                    option = data['visible'][index]
                except (ValueError, IndexError, KeyError):
                    return self._choices(message, stage, data, now)
                selected = set(data[stage])
                if option['key'] in selected:
                    selected.remove(option['key'])
                elif len(selected)<20:
                    selected.add(option['key'])
                data[stage] = sorted(selected)
                return self._choices(message, stage, data, now)
            if action=='page':
                data['page'] += 1
                return self._choices(message, stage, data, now)
            if action=='next':
                return self._sections(message, data, now)
        if stage=='premieres' and action in {'yes','no','skip'}:
            if action!='skip':
                data['premieres'] = action=='yes'
            return self._sections(message, data, now)
        if stage=='marketing' and action in {'allow','decline'}:
            if action=='decline':
                return self.decline(message, now)
            try:
                save_preferences(self.db, self.theatre, self.protector, message.channel, message.external_user_id,
                    message.display_name, data['genres'], data['artists'], data['premieres'], action=='allow',
                    DOCUMENT_VERSIONS['marketing'], now=now, survey_actor=self._actor(message), survey_token=data['token'])
            except LookupError:
                return OutgoingMessage(message.channel, message.external_user_id,
                    Reply('Эта кнопка анкеты уже неактуальна. Напишите «Анкета интересов», чтобы открыть её заново.'))
            except PermissionError:
                return self._consent(message, {}, now)
            except ValueError:
                self.close(message, now)
                return OutgoingMessage(message.channel, message.external_user_id,
                    Reply('Список жанров или артистов изменился. Напишите «Анкета интересов» и выберите интересы заново.'))
            return self._menu_reply(message, 'Интересы сохранены. Рекламные предложения разрешены.')
        return OutgoingMessage(message.channel, message.external_user_id,
            Reply('Это действие сейчас недоступно. Напишите «Анкета интересов», чтобы начать заново.'))

    def _consent(self, message, data, now):
        self._save(message, 'consent', data, now)
        return self._reply(message, data,
            'Перед заполнением ознакомьтесь с политикой и подтвердите согласие на обработку персональных данных.',
            [('legal','Открыть документы'),('accept','Согласен'),('cancel','Отмена')])
