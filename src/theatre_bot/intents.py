from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class Intent(StrEnum):
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
    (Intent.SUPPORT, (r"\bподдержк", r"\bпомог", r"\bошибк", r"\bжалоб")),
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
    return " ".join(text.lower().strip().split())


def detect_intent(text: str) -> IntentResult:
    normalized = normalize(text)
    for intent, patterns in _RULES:
        if any(re.search(pattern, normalized) for pattern in patterns):
            return IntentResult(intent=intent, confidence=1.0)
    return IntentResult(intent=Intent.UNKNOWN, confidence=0.0)
