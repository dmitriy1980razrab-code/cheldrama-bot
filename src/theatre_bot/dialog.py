from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
import sqlite3

from theatre_bot.intents import Intent, detect_intent
from theatre_bot.queries import (
    Performance,
    between_dates,
    cast_for_play,
    artist_repertoire,
    find_artists_in_text,
    find_play_in_text,
    nearest,
    new_year_performances,
    plays_by_genre,
    upcoming_for_play,
    upcoming_for_artist,
)


THEATRE_TIMEZONE = timezone(timedelta(hours=5))


@dataclass(frozen=True)
class Card:
    title: str
    subtitle: str
    details: str
    play_url: str
    ticket_event_id: str | None


@dataclass(frozen=True)
class Reply:
    text: str
    cards: tuple[Card, ...] = ()


def theatre_now() -> datetime:
    return datetime.now(THEATRE_TIMEZONE).replace(tzinfo=None)


def _requested_date(text: str, today: date) -> date | None:
    normalized = text.casefold().replace("ё", "е")
    if "послезавтра" in normalized:
        return today + timedelta(days=2)
    if "завтра" in normalized:
        return today + timedelta(days=1)
    if "сегодня" in normalized:
        return today
    match = re.search(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?(?!\d)", normalized)
    if not match:
        return None
    day, month = map(int, match.group(1, 2))
    year = int(match.group(3)) if match.group(3) else today.year
    candidate = date(year, month, day)
    if not match.group(3) and candidate < today:
        candidate = date(year + 1, month, day)
    return candidate


def _weekend(today: date) -> tuple[date, date]:
    days_until_saturday = (5 - today.weekday()) % 7
    saturday = today + timedelta(days=days_until_saturday)
    return saturday, saturday + timedelta(days=1)


WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "сред": 2,
    "четверг": 3,
    "пятниц": 4,
    "суббот": 5,
    "воскресен": 6,
}

GENRES = {
    "комед": "комед",
    "мелодрам": "мелодрам",
    "драм": "драм",
    "трагед": "трагед",
    "сказк": "сказк",
    "мюзикл": "мюзикл",
    "музыкальн": "музыкальн",
}


def _weekday_date(text: str, today: date) -> date | None:
    normalized = text.casefold().replace("ё", "е")
    weekday = next((number for stem, number in WEEKDAYS.items() if stem in normalized), None)
    if weekday is None:
        return None
    monday = today - timedelta(days=today.weekday())
    if "следующ" in normalized:
        monday += timedelta(days=7)
    return monday + timedelta(days=weekday)


def _genre_fragment(text: str) -> str | None:
    normalized = text.casefold().replace("ё", "е")
    return next((value for stem, value in GENRES.items() if stem in normalized), None)


def _new_year_period(today: date) -> tuple[date, date]:
    if today.month == 1 and today.day <= 10:
        return date(today.year - 1, 12, 20), date(today.year, 1, 10)
    return date(today.year, 12, 20), date(today.year + 1, 1, 10)


def _card(performance: Performance) -> Card:
    subtitle = performance.starts_at.strftime("%d.%m.%Y в %H:%M")
    details = " · ".join(
        value for value in (performance.genre, performance.age_rating, performance.venue) if value
    )
    return Card(
        title=performance.title,
        subtitle=subtitle,
        details=details,
        play_url=performance.play_url,
        ticket_event_id=performance.ticket_event_id,
    )


def _reply_for_performances(items: list[Performance], heading: str, empty: str) -> Reply:
    if not items:
        return Reply(text=empty)
    return Reply(text=heading, cards=tuple(_card(item) for item in items))


def answer(connection: sqlite3.Connection, text: str, now: datetime | None = None) -> Reply:
    current = now or theatre_now()
    intent = detect_intent(text).intent
    play = find_play_in_text(connection, text)
    artists = find_artists_in_text(connection, text)

    if play is not None and intent == Intent.PLAY_CAST:
        cast = cast_for_play(connection, play.id)
        if not cast:
            return Reply(text=f"Состав спектакля «{play.title}» пока не загружен.")
        lines = [f"• {role}: {artist}" if role else f"• {artist}" for role, artist in cast]
        return Reply(text=f"Действующие лица и исполнители спектакля «{play.title}»:\n" + "\n".join(lines))

    if play is not None and intent == Intent.PLAY_INFO:
        parts = [f"«{play.title}»"]
        if play.summary:
            parts.append(play.summary)
        if play.director:
            parts.append(f"Режиссёр-постановщик — {play.director}.")
        return Reply(text="\n\n".join(parts))

    if play is not None:
        return _reply_for_performances(
            upcoming_for_play(connection, play.id, current, limit=3),
            f"Ближайшие показы спектакля «{play.title}»:",
            f"Ближайших показов спектакля «{play.title}» в афише нет.",
        )

    if len(artists) > 1:
        names = "\n".join(f"• {artist.full_name}" for artist in artists)
        return Reply(text="Нашлось несколько артистов. Уточните имя:\n" + names)

    if len(artists) == 1:
        artist = artists[0]
        normalized = text.casefold().replace("ё", "е")
        wants_nearest = "когда" in normalized or "ближайш" in normalized
        if wants_nearest:
            items = upcoming_for_artist(connection, artist.id, current, limit=3)
            if not items:
                return Reply(text=f"Ближайших спектаклей с участием {artist.full_name} в афише нет.")
            cards = []
            for performance, role in items:
                card = _card(performance)
                role_text = f"Роль: {role}" if role else ""
                details = " · ".join(value for value in (role_text, card.details) if value)
                cards.append(Card(card.title, card.subtitle, details, card.play_url, card.ticket_event_id))
            return Reply(
                text=f"Артист: {artist.full_name}. Ближайшие спектакли:",
                cards=tuple(cards),
            )

        repertoire = artist_repertoire(connection, artist.id)
        if not repertoire:
            return Reply(text=f"Действующих спектаклей с участием {artist.full_name} не найдено.")
        lines = [f"• {title} — {role}" if role else f"• {title}" for title, role in repertoire]
        return Reply(text=f"{artist.full_name} участвует в спектаклях:\n" + "\n".join(lines))

    if intent == Intent.GENRE:
        fragment = _genre_fragment(text)
        items = plays_by_genre(connection, fragment or "") if fragment else []
        if not items:
            return Reply(text="Спектаклей этого жанра в действующем репертуаре не найдено.")
        lines = [f"• {title} — {genre}" for title, genre in items]
        return Reply(text="В действующем репертуаре:\n" + "\n".join(lines))

    if intent == Intent.NEW_YEAR:
        first_day, last_day = _new_year_period(current.date())
        items = new_year_performances(connection, first_day, last_day)
        if not items:
            return Reply(text="Новогодние спектакли на этот период пока не опубликованы.")
        lines = [
            f"• {item.starts_at.strftime('%d.%m.%Y в %H:%M')} — {item.title}"
            for item in items
        ]
        return Reply(text="Новогодние сказки с 20 декабря по 10 января:\n" + "\n".join(lines))

    if intent == Intent.SCHEDULE_NEAREST:
        return _reply_for_performances(
            nearest(connection, current, limit=3),
            "Ближайшие встречи на нашей сцене:",
            "В ближайшее время спектаклей в афише не найдено.",
        )

    if intent == Intent.SCHEDULE_DATE:
        requested = _requested_date(text, current.date())
        if requested is None:
            return Reply(text="Уточните дату, пожалуйста, например: 25.09 или завтра.")
        return _reply_for_performances(
            between_dates(connection, requested, requested),
            f"Спектакли {requested.strftime('%d.%m.%Y')}:",
            "На выбранную дату спектаклей в афише нет.",
        )

    if intent == Intent.SCHEDULE_WEEKEND:
        saturday, sunday = _weekend(current.date())
        return _reply_for_performances(
            between_dates(connection, saturday, sunday),
            "В ближайшие выходные будем рады видеть вас на спектаклях:",
            "На ближайшие выходные спектаклей в афише нет.",
        )

    if intent == Intent.SCHEDULE_WEEKDAY:
        requested = _weekday_date(text, current.date())
        if requested is None:
            return Reply(text="Уточните день недели, пожалуйста.")
        return _reply_for_performances(
            between_dates(connection, requested, requested),
            f"Спектакли {requested.strftime('%d.%m.%Y')}:",
            "На указанный день спектаклей в афише нет.",
        )

    if intent == Intent.SCHEDULE_WEEK:
        normalized = text.casefold().replace("ё", "е")
        monday = current.date() - timedelta(days=current.date().weekday())
        if "следующ" in normalized:
            monday += timedelta(days=7)
        sunday = monday + timedelta(days=6)
        return _reply_for_performances(
            between_dates(connection, monday, sunday),
            "Спектакли на выбранной неделе:",
            "На выбранной неделе спектаклей в афише нет.",
        )

    return Reply(
        text=(
            "Я помогу узнать ближайшие спектакли и расписание. "
            "Например, спросите: «Что идёт завтра?» или «Что посмотреть на выходных?»"
        )
    )
