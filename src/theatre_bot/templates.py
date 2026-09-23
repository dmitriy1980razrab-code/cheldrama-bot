from __future__ import annotations

import sqlite3


DEFAULT_TEMPLATES: dict[tuple[str, str], str] = {
    ("greeting", "default"): (
        "Здравствуйте! Разрешите пригласить Вас в мир театра имени Н. Орлова 🎭"
    ),
    ("help", "default"): (
        "Вы можете спросить о ближайших спектаклях, конкретной дате, жанре, "
        "возрасте зрителя, постановке или артисте. Например: «Что идёт завтра?», "
        "«Что посмотреть ребёнку 10 лет?» или «Где играет Мартынов?»"
    ),
    ("fallback", "default"): (
        "Пока я не смог понять вопрос. Попробуйте указать название спектакля, "
        "фамилию артиста, дату или жанр."
    ),
    ("schedule", "nearest"): "Ближайшие встречи на нашей сцене:",
    ("schedule", "weekend"): "В ближайшие выходные будем рады видеть вас на спектаклях:",
    ("schedule", "week"): "На выбранной неделе театр приглашает вас на спектакли:",
    ("schedule", "date"): "Спектакли {date}:",
    ("play", "upcoming"): "Будем рады видеть вас на спектакле «{title}». Ближайшие показы:",
    ("artist", "upcoming"): "Артист: {artist}. Ближайшие спектакли с его участием:",
    ("genre", "list"): "В действующем репертуаре представлены:",
    ("new_year", "list"): "Новогодние сказки с 20 декабря по 10 января:",
}

LEGACY_TEMPLATES: dict[tuple[str, str], tuple[str, ...]] = {
    ("greeting", "default"): (
        (
            "Здравствуйте! Разрешите пригласить Вас в мир театра 🎭\n\n"
            "Я помогу Вам выбрать спектакль, узнать расписание и познакомиться "
            "с артистами Челябинского театра драмы имени Наума Орлова."
        ),
        (
            "Добро пожаловать в Челябинский театр драмы имени Наума Орлова. "
            "Я помогу выбрать спектакль, узнать расписание и состав исполнителей."
        ),
    ),
    ("help", "default"): (
        (
            "Вы можете спросить о ближайших спектаклях, конкретной дате, жанре, "
            "постановке или артисте. Например: «Что идёт завтра?», «Покажите комедии» "
            "или «Где играет Мартынов?»"
        ),
    ),
}


def seed_templates(connection: sqlite3.Connection) -> None:
    for (intent, variant), template in DEFAULT_TEMPLATES.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO response_templates (intent, channel, variant, template)
            VALUES (?, 'all', ?, ?)
            """,
            (intent, variant, template),
        )
        legacy_values = LEGACY_TEMPLATES.get((intent, variant), ())
        for legacy in legacy_values:
            connection.execute(
                """
                UPDATE response_templates SET template = ?
                WHERE intent = ? AND channel = 'all' AND variant = ? AND template = ?
                """,
                (template, intent, variant, legacy),
            )


def render_template(
    connection: sqlite3.Connection,
    intent: str,
    variant: str = "default",
    channel: str = "all",
    **values,
) -> str:
    row = connection.execute(
        """
        SELECT template FROM response_templates
        WHERE intent = ? AND variant = ? AND channel IN (?, 'all') AND is_active = 1
        ORDER BY CASE WHEN channel = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (intent, variant, channel, channel),
    ).fetchone()
    template = row["template"] if row else DEFAULT_TEMPLATES.get((intent, variant), "")
    try:
        return template.format(**values)
    except KeyError:
        return template
