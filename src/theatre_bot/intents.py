from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from difflib import SequenceMatcher


class Intent(StrEnum):
    GREETING = "greeting"
    HELP = "help"
    SCHEDULE_NEAREST = "schedule.nearest"
    SCHEDULE_DATE = "schedule.date"
    SCHEDULE_WEEKEND = "schedule.weekend"
    SCHEDULE_WEEK = "schedule.week"
    SCHEDULE_WEEKDAY = "schedule.weekday"
    GENRE = "genre"
    NEW_YEAR = "new_year"
    PLAY_INFO = "play.info"
    PLAY_CAST = "play.cast"
    ARTIST_PLAYS = "artist.plays"
    TICKET = "ticket"
    SUPPORT = "support"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float


_RULES: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.GREETING, (r"\bпривет", r"\bздравств", r"\bдоброе утро\b", r"\bдобрый день\b", r"\bдобрый вечер\b")),
    (Intent.HELP, (r"\bчто (?:ты )?умеешь\b", r"\bкак пользоваться\b", r"\bпомощь\b", r"\bменю\b")),
    (Intent.SUPPORT, (r"\bподдержк", r"\bошибк", r"\bжалоб")),
    (Intent.TICKET, (r"\bбилет", r"\bкупить", r"\bцена", r"\bместа\b")),
    (Intent.SCHEDULE_WEEKEND, (r"\bвыходн",)),
    (Intent.SCHEDULE_WEEKDAY, (r"\bпонедельник", r"\bвторник", r"\bсред[ауе]", r"\bчетверг", r"\bпятниц", r"\bсуббот", r"\bвоскресен")),
    (Intent.SCHEDULE_WEEK, (r"\bна (?:этой |следующей )?неделе\b",)),
    (Intent.SCHEDULE_DATE, (r"\bсегодня\b", r"\bзавтра\b", r"\bпослезавтра\b", r"\d{1,2}[./]\d{1,2}")),
    (Intent.NEW_YEAR, (r"\bновогодн.*(?:сказк|кампан|компан)",)),
    (Intent.GENRE, (r"\bкомед", r"\bмелодрам", r"\bдрам", r"\bтрагед", r"\bсказк", r"\bмюзикл", r"\bмузыкальн")),
    (Intent.PLAY_CAST, (r"\bкто играет\b", r"\bкто участвует\b", r"\bсостав\b", r"\bзанят в\b")),
    (Intent.ARTIST_PLAYS, (r"\bгде играет\b", r"\bспектакли с участием\b", r"\bлюбимый артист\b")),
    (Intent.PLAY_INFO, (r"\bо ч[её]м\b", r"\bописание\b", r"\bаннотац", r"\bрежисс[её]р")),
    (Intent.SCHEDULE_NEAREST, (r"\bафиша\b", r"\bчто ид[её]т\b", r"\bближайш", r"\bрасписан")),
)


def normalize(text: str) -> str:
    value = text.casefold().replace("ё", "е")
    return " ".join(re.findall(r"[a-zа-я0-9+./-]+", value))


def _fuzzy_token(tokens: list[str], targets: tuple[str, ...], threshold: float = 0.82) -> bool:
    return any(
        len(token) >= 4 and SequenceMatcher(None, token, target).ratio() >= threshold
        for token in tokens
        for target in targets
    )


def detect_intent(text: str) -> IntentResult:
    normalized = normalize(text)
    for intent, patterns in _RULES:
        if any(re.search(pattern, normalized) for pattern in patterns):
            return IntentResult(intent=intent, confidence=1.0)
    tokens = normalized.split()
    fuzzy_rules = (
        (Intent.NEW_YEAR, ("новогодняя", "новогодние")),
        (Intent.SCHEDULE_DATE, ("сегодня", "завтра", "послезавтра")),
        (Intent.SCHEDULE_WEEKEND, ("выходных", "выходные")),
        (Intent.TICKET, ("билет", "купить")),
        (Intent.GENRE, ("комедия", "драма", "мелодрама", "сказка", "мюзикл")),
        (Intent.SCHEDULE_NEAREST, ("афиша", "расписание", "ближайшие")),
    )
    for intent, targets in fuzzy_rules:
        if _fuzzy_token(tokens, targets):
            return IntentResult(intent=intent, confidence=0.8)
    return IntentResult(intent=Intent.UNKNOWN, confidence=0.0)
