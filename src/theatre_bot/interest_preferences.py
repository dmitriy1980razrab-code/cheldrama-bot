"""Explicit questionnaire preferences and atomic saving of advertising permission."""
import json
from datetime import datetime, timezone, timedelta
from theatre_bot.marketing_reports import preference_snapshot, record_preference_changes
from theatre_bot.subscribers import _has_current_consent, _get_or_create_subscriber, _timestamp


GENRE_CATEGORIES = (
    ('комедия', 'Комедия'),
    ('драма', 'Драма'),
    ('трагедия', 'Трагедия'),
    ('мелодрама', 'Мелодрама'),
    ('музыкальный спектакль', 'Музыкальный спектакль'),
    ('сказка', 'Сказка'),
)

def genre_options(theatre):
    """Use a small fixed vocabulary rather than publishing author subtitles."""
    return [{'key': key, 'label': label} for key, label in GENRE_CATEGORIES]



def artist_options(theatre):
    return [{'key': row['source_url'], 'label': row['full_name']} for row in theatre.execute(
        'SELECT source_url, full_name FROM artists WHERE is_active=1 ORDER BY full_name, id')]


def play_options(theatre):
    return [{'key': row['source_url'], 'label': row['title']} for row in theatre.execute(
        'SELECT source_url, title FROM plays WHERE is_active=1 ORDER BY title, id')]


def season_year(now=None):
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    day = moment.astimezone(timezone(timedelta(hours=5))).date()
    return day.year if (day.month, day.day) >= (7, 11) else day.year-1


def personal_data_granted(db, protector, channel, external_id):
    row = db.execute('SELECT id FROM subscribers WHERE channel=? AND external_id_hash=?',
                     (channel, protector.lookup_hash(channel, external_id))).fetchone()
    return bool(row and _has_current_consent(db, row['id'], 'personal_data'))


def current_preferences(db, protector, channel, external_id, now=None):
    row = db.execute('SELECT id, interested_in_premieres FROM subscribers WHERE channel=? AND external_id_hash=?',
                     (channel, protector.lookup_hash(channel, external_id))).fetchone()
    if row is None:
        return {'genres': [], 'artists': [], 'plays': [], 'premieres': False}
    rows = db.execute("SELECT preference_type, preference_key FROM preferences WHERE subscriber_id=? AND source='interest_survey'", (row['id'],)).fetchall()
    return {'genres': [r['preference_key'] for r in rows if r['preference_type']=='genre'],
            'artists': [r['preference_key'] for r in rows if r['preference_type']=='artist'],
            'plays': [r[0] for r in db.execute('SELECT play_key FROM survey_play_interests WHERE subscriber_id=? AND season_start_year=?',(row['id'],season_year(now)))],
            'premieres': bool(row['interested_in_premieres'])}


def save_preferences(db, theatre, protector, channel, external_id, display_name,
                     genres, artists, premieres, marketing, document_version, now=None,
                     *, survey_actor=None, survey_token=None, plays=None):
    if not marketing:
        raise ValueError('advertising permission required to save interests')
    if not personal_data_granted(db, protector, channel, external_id):
        raise PermissionError('personal data consent required')
    catalog = {'genre': {v['key']: v['label'] for v in genre_options(theatre)},
               'artist': {v['key']: v['label'] for v in artist_options(theatre)}}
    selected = {'genre': set(genres), 'artist': set(artists)}
    if any(len(values)>20 or not values.issubset(catalog[kind]) for kind, values in selected.items()):
        raise ValueError('selection is no longer available')
    play_catalog={v['key']:v['label'] for v in play_options(theatre)}
    play_selection=None if plays is None else set(plays)
    if play_selection is not None and (len(play_selection)>20 or not play_selection.issubset(play_catalog)):
        raise ValueError('play selection is no longer available')
    db.execute('BEGIN IMMEDIATE')
    try:
        if survey_actor is not None:
            survey = db.execute('SELECT stage, payload_encrypted, expires_at FROM interest_survey_state WHERE actor_hash=?', (survey_actor,)).fetchone()
            if not survey or survey['stage']!='marketing' or survey['payload_encrypted'] is None or survey['expires_at']<=_timestamp(now):
                raise LookupError('questionnaire is stale')
            if json.loads(protector.decrypt(survey['payload_encrypted'])).get('token') != survey_token:
                raise LookupError('questionnaire is stale')
        if not personal_data_granted(db, protector, channel, external_id):
            raise PermissionError('personal data consent required')
        subscriber_id = _get_or_create_subscriber(db, protector, channel, external_id, now, display_name)
        stamp = _timestamp(now)
        before = preference_snapshot(db, subscriber_id)
        if play_selection is not None:
            db.execute('DELETE FROM survey_play_interests WHERE subscriber_id=?',(subscriber_id,))
            for key in sorted(play_selection):
                db.execute('INSERT INTO survey_play_interests VALUES (?,?,?,?,?)',
                    (subscriber_id,key,play_catalog[key],season_year(now),stamp))
        db.execute("UPDATE subscribers SET status='active', interested_in_premieres=?, updated_at=? WHERE id=?",
                   (int(premieres), stamp, subscriber_id))
        db.execute("DELETE FROM preferences WHERE subscriber_id=? AND source='interest_survey' AND preference_type IN ('genre','artist')", (subscriber_id,))
        for kind, keys in selected.items():
            for key in sorted(keys):
                db.execute('''INSERT INTO preferences
                    (subscriber_id, preference_type, preference_key, preference_label, source, recorded_at)
                    VALUES (?, ?, ?, ?, 'interest_survey', ?)
                    ON CONFLICT(subscriber_id, preference_type, preference_key) DO UPDATE SET
                      preference_label=excluded.preference_label, source=excluded.source, recorded_at=excluded.recorded_at''',
                    (subscriber_id, kind, key, catalog[kind][key], stamp))
        db.execute('''INSERT INTO consent_events
            (subscriber_id, consent_type, action, document_version, source, recorded_at)
            VALUES (?, 'marketing', ?, ?, 'interest_survey', ?)''',
            (subscriber_id, 'granted' if marketing else 'revoked', document_version, stamp))
        record_preference_changes(db, subscriber_id, before, preference_snapshot(db, subscriber_id), stamp)
        if survey_actor is not None:
            db.execute("UPDATE interest_survey_state SET stage='completed', payload_encrypted=NULL, decline_count=0, next_offer_at=NULL, updated_at=? WHERE actor_hash=?", (stamp, survey_actor))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return subscriber_id


def delete_preferences(db, protector, channel, external_id, document_version,
                       survey_actor, survey_token, now=None):
    """Delete survey interests and revoke advertising atomically, with stale-button protection."""
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT stage, payload_encrypted, expires_at FROM interest_survey_state WHERE actor_hash=?', (survey_actor,)).fetchone()
        if (not state or state['stage'] != 'delete_confirm' or not state['payload_encrypted']
                or state['expires_at'] <= _timestamp(now)
                or json.loads(protector.decrypt(state['payload_encrypted'])).get('token') != survey_token):
            raise LookupError('questionnaire is stale')
        row = db.execute('SELECT id FROM subscribers WHERE channel=? AND external_id_hash=?',
                         (channel, protector.lookup_hash(channel, external_id))).fetchone()
        stamp = _timestamp(now)
        if row:
            before = preference_snapshot(db, row['id'])
            db.execute('DELETE FROM survey_play_interests WHERE subscriber_id=?',(row['id'],))
            db.execute("DELETE FROM preferences WHERE subscriber_id=? AND source='interest_survey'", (row['id'],))
            db.execute('UPDATE subscribers SET interested_in_premieres=0, updated_at=? WHERE id=?', (stamp, row['id']))
            db.execute("""INSERT INTO consent_events
                (subscriber_id, consent_type, action, document_version, source, recorded_at)
                VALUES (?, 'marketing', 'revoked', ?, 'interest_survey', ?)""",
                (row['id'], document_version, stamp))
            record_preference_changes(db, row['id'], before, set(), stamp)
        db.execute("""UPDATE interest_survey_state SET stage='closed', payload_encrypted=NULL,
            next_offer_at=NULL, decline_count=0, updated_at=? WHERE actor_hash=?""", (stamp, survey_actor))
        db.commit()
    except BaseException:
        db.rollback()
        raise
