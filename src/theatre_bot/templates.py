from __future__ import annotations

import sqlite3


DEFAULT_TEMPLATES: dict[tuple[str, str], str] = {
    ("greeting", "default"): (
        "Добро пожаловать в Челябинский театр драмы имени Наума Орлова. "
        "Я помогу выбрать спектакль, узнать расписание и состав исполнителей."
    ),
    ("help", "default"): (
        "Вы можете спросить о ближайших спектаклях, конкретной дате, жанре, "
        "постановке или артисте. Например: «Что идёт завтра?», «Покажите комедии» "
        "или «Где играет Мартынов?»"
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


def seed_templates(connection: sqlite3.Connection) -> None:
    for (intent, variant), template in DEFAULT_TEMPLATES.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO response_templates (intent, channel, variant, template)
            VALUES (?, 'all', ?, ?)
            """,
            (intent, variant, template),
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

