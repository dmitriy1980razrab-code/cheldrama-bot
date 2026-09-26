from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import secrets
import sqlite3
import threading

from cryptography.fernet import Fernet

from theatre_bot.campaigns import (
    approve_campaign,
    create_campaign,
    preview_campaign,
    schedule_campaign,
)
from theatre_bot.demo_channel import _add_demo_subscriber
from theatre_bot.subscribers import (
    IdentityProtector,
    connect_subscribers,
    initialize_subscribers,
    list_active_subscriber_profiles,
    subscriber_stats,
)


SESSION_LIFETIME = timedelta(minutes=30)
MAX_BODY_BYTES = 16_384
THEATRE_TIMEZONE = timezone(timedelta(hours=5))


@dataclass(frozen=True)
class AdminSession:
    csrf_token: str
    expires_at: datetime


class AdminDemoSecurity:
    def __init__(self, password: str) -> None:
        self._password = password
        self._sessions: dict[str, AdminSession] = {}
        self._lock = threading.Lock()

    def login(self, password: str, now: datetime | None = None) -> tuple[str, str] | None:
        if not secrets.compare_digest(password, self._password):
            return None
        current = now or datetime.now(timezone.utc)
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = AdminSession(
                csrf_token, current + SESSION_LIFETIME
            )
        return session_id, csrf_token

    def session(self, session_id: str | None, now: datetime | None = None) -> AdminSession | None:
        if not session_id:
            return None
        current = now or datetime.now(timezone.utc)
        with self._lock:
            value = self._sessions.get(session_id)
            if value is None or value.expires_at <= current:
                self._sessions.pop(session_id, None)
                return None
            return value

    def valid_csrf(self, session_id: str | None, token: str) -> bool:
        value = self.session(session_id)
        return bool(value and secrets.compare_digest(value.csrf_token, token))


def seed_admin_demo(database_path: Path, protector: IdentityProtector) -> None:
    connection = connect_subscribers(database_path)
    initialize_subscribers(connection)
    if connection.execute("SELECT count(*) FROM subscribers").fetchone()[0]:
        connection.close()
        return
    now = datetime.now(timezone.utc)
    _add_demo_subscriber(
        connection, protector, "vk", "demo-vk-anna", "Анна",
        "hamlet", "Гамлет", True, now,
    )
    _add_demo_subscriber(
        connection, protector, "max", "demo-max-boris", "Борис",
        "seagull", "Чайка", True, now,
    )
    _add_demo_subscriber(
        connection, protector, "vk", "demo-vk-maria", "Мария",
        "hamlet", "Гамлет", False, now,
    )
    connection.close()


def _page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(title)}</title><style>
body{{font:16px Arial,sans-serif;margin:0;background:#f4f7fb;color:#111}}
header{{background:#1769aa;color:#fff;padding:18px 5%}}main{{max-width:1100px;margin:24px auto;padding:0 20px}}
.box{{background:#fff;border-radius:12px;padding:20px;margin-bottom:18px;box-shadow:0 2px 12px #0001}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #dde5ee;text-align:left}}
input,select,textarea,button{{font:inherit;padding:9px;margin:5px 0;box-sizing:border-box}}textarea{{width:100%;min-height:90px}}
button{{background:#1769aa;color:#fff;border:0;border-radius:7px;cursor:pointer}}.note{{color:#4d5b6a}}
.ok{{background:#e8f5eb;padding:12px;border-radius:8px}}label{{display:block;margin-top:8px}}
</style></head><body><header><h1>Ваш капельдинер — администрация</h1></header><main>{body}</main></body></html>""".encode("utf-8")


def render_login(error: bool = False) -> bytes:
    message = '<p class="note">Неверный пароль.</p>' if error else ""
    return _page(
        "Вход",
        f'<section class="box"><h2>Вход в демонстрационную панель</h2>{message}'
        '<form method="post" action="/login"><label>Одноразовый пароль</label>'
        '<input name="password" type="password" required autocomplete="current-password"> '
        '<button type="submit">Войти</button></form>'
        '<p class="note">Используются только вымышленные данные. Панель доступна только на этом компьютере.</p></section>',
    )


def render_dashboard(connection: sqlite3.Connection, protector: IdentityProtector, csrf: str) -> bytes:
    profiles = list_active_subscriber_profiles(
        connection, protector, "demo-admin", "dashboard_view"
    )
    stats = subscriber_stats(connection)
    rows = []
    for profile in profiles:
        preferences = ", ".join(label for _, label in profile.preferences) or "—"
        marketing = "да" if profile.consents.get("marketing") else "нет"
        rows.append(
            "<tr>"
            f"<td>{escape(profile.display_name or '—')}</td>"
            f"<td>{escape(profile.channel.upper())}</td>"
            f"<td>{escape(profile.external_id)}</td>"
            f"<td>{escape(preferences)}</td><td>{marketing}</td></tr>"
        )
    campaign_rows = []
    status_names = {
        "draft": "черновик",
        "approved": "подтверждена",
        "scheduled": "запланирована",
        "cancelled": "отменена",
        "completed": "завершена",
    }
    default_schedule = (datetime.now(THEATRE_TIMEZONE) + timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M"
    )
    for campaign in connection.execute(
        "SELECT * FROM campaigns ORDER BY id DESC LIMIT 20"
    ).fetchall():
        action = "—"
        if campaign["status"] == "draft":
            action = (
                '<form method="post" action="/campaign/approve">'
                f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
                f'<input type="hidden" name="campaign_id" value="{campaign["id"]}">'
                '<button type="submit">Подтвердить</button></form>'
            )
        elif campaign["status"] == "approved":
            action = (
                '<form method="post" action="/campaign/schedule">'
                f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
                f'<input type="hidden" name="campaign_id" value="{campaign["id"]}">'
                f'<input type="datetime-local" name="scheduled_at" value="{default_schedule}" required>'
                '<button type="submit">Запланировать</button></form>'
            )
        campaign_rows.append(
            "<tr>"
            f'<td>{campaign["id"]}</td><td>{escape(campaign["name"])}</td>'
            f'<td>{escape(campaign["target_label"] or "Все")}</td>'
            f'<td>{escape(status_names.get(campaign["status"], campaign["status"]))}</td>'
            f'<td>{escape(campaign["scheduled_at"] or "—")}</td><td>{action}</td></tr>'
        )
    body = f"""
<section class="box"><h2>Сводка</h2><p>Активных подписчиков: {sum(stats.active_by_channel.values())}.
С рекламным согласием: {stats.marketing_consents}.</p></section>
<section class="box"><h2>Подписчики</h2><table><thead><tr><th>Имя</th><th>Канал</th><th>ID</th>
<th>Предпочтения</th><th>Реклама разрешена</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section class="box"><h2>Кампании</h2><table><thead><tr><th>ID</th><th>Название</th><th>Интерес</th>
<th>Статус</th><th>Отправка</th><th>Действие</th></tr></thead><tbody>{''.join(campaign_rows) or '<tr><td colspan="6">Кампаний пока нет</td></tr>'}</tbody></table></section>
<section class="box"><h2>Новая тестовая кампания</h2><form method="post" action="/campaign/preview">
<input type="hidden" name="csrf" value="{escape(csrf)}"><label>Название</label>
<input name="name" required value="Предложение зрителям"><label>Канал</label>
<select name="channel"><option value="all">VK и MAX</option><option value="vk">VK</option><option value="max">MAX</option></select>
<label>Интерес</label><select name="target"><option value="hamlet|Гамлет">Гамлет</option>
<option value="seagull|Чайка">Чайка</option></select><label>Сообщение</label>
<textarea name="message" required>Будем рады видеть Вас на спектакле!</textarea>
<button type="submit">Показать получателей</button></form></section>"""
    return _page("Административная панель", body)


def render_preview(name: str, campaign_id: int, recipients, csrf: str) -> bytes:
    rows = "".join(
        f"<li>{escape(item.display_name or '—')} — {escape(item.channel.upper())} — {escape(item.external_id)}</li>"
        for item in recipients
    ) or "<li>Подходящих получателей нет</li>"
    return _page(
        "Предварительный просмотр",
        f'<section class="box"><h2>{escape(name)}</h2><p class="ok">Получателей: {len(recipients)}</p>'
        f"<ul>{rows}</ul><p class='note'>Сообщения не отправлены. Это только проверка аудитории.</p>"
        '<form method="post" action="/campaign/approve">'
        f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
        f'<input type="hidden" name="campaign_id" value="{campaign_id}">'
        '<button type="submit">Подтвердить кампанию</button></form>'
        '<p><a href="/admin">Вернуться без подтверждения</a></p></section>',
    )


def parse_scheduled_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid schedule date") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=THEATRE_TIMEZONE)
    return parsed


def create_admin_demo_handler(
    database_path: Path,
    protector: IdentityProtector,
    security: AdminDemoSecurity,
):
    class AdminDemoHandler(BaseHTTPRequestHandler):
        server_version = "TheatreAdminDemo/0.1"

        def _session_id(self) -> str | None:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            item = cookie.get("admin_demo_session")
            return item.value if item else None

        def _send(self, status: HTTPStatus, body: bytes, cookie: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, target: str, cookie: str | None = None) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def _form(self) -> dict[str, str] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if length <= 0 or length > MAX_BODY_BYTES:
                return None
            parsed = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            return {key: values[0] for key, values in parsed.items()}

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/login":
                self._send(HTTPStatus.OK, render_login())
                return
            if path != "/admin":
                self._redirect("/admin")
                return
            session = security.session(self._session_id())
            if session is None:
                self._redirect("/login")
                return
            connection = connect_subscribers(database_path)
            initialize_subscribers(connection)
            try:
                body = render_dashboard(connection, protector, session.csrf_token)
            finally:
                connection.close()
            self._send(HTTPStatus.OK, body)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            form = self._form()
            if form is None:
                self._send(HTTPStatus.BAD_REQUEST, _page("Ошибка", "<p>Некорректный запрос.</p>"))
                return
            if path == "/login":
                result = security.login(form.get("password", ""))
                if result is None:
                    self._send(HTTPStatus.UNAUTHORIZED, render_login(True))
                    return
                session_id, _ = result
                cookie = f"admin_demo_session={session_id}; HttpOnly; SameSite=Strict; Path=/; Max-Age=1800"
                self._redirect("/admin", cookie)
                return
            session_id = self._session_id()
            if path not in {"/campaign/preview", "/campaign/approve", "/campaign/schedule"} or not security.valid_csrf(
                session_id, form.get("csrf", "")
            ):
                self._send(HTTPStatus.FORBIDDEN, _page("Доступ запрещён", "<p>Проверка безопасности не пройдена.</p>"))
                return
            connection = connect_subscribers(database_path)
            initialize_subscribers(connection)
            try:
                if path == "/campaign/preview":
                    target_parts = form.get("target", "").split("|", 1)
                    if len(target_parts) != 2:
                        raise ValueError("invalid target")
                    campaign_id = create_campaign(
                        connection,
                        form.get("name", ""),
                        form.get("message", ""),
                        "demo-admin",
                        channel=form.get("channel", "all"),
                        target_type="play",
                        target_key=target_parts[0],
                        target_label=target_parts[1],
                    )
                    preview = preview_campaign(
                        connection, protector, campaign_id,
                        "demo-admin", "browser_demo_preview",
                    )
                    session = security.session(session_id)
                    body = render_preview(
                        form.get("name", ""), campaign_id, preview.recipients,
                        session.csrf_token if session else "",
                    )
                elif path == "/campaign/approve":
                    approve_campaign(
                        connection, int(form.get("campaign_id", "")), "demo-director"
                    )
                    self._redirect("/admin")
                    return
                else:
                    schedule_campaign(
                        connection,
                        int(form.get("campaign_id", "")),
                        parse_scheduled_at(form.get("scheduled_at", "")),
                        "demo-admin",
                    )
                    self._redirect("/admin")
                    return
            except (ValueError, LookupError, sqlite3.Error):
                body = _page("Ошибка", "<p>Не удалось подготовить кампанию.</p>")
                self._send(HTTPStatus.BAD_REQUEST, body)
                return
            finally:
                connection.close()
            self._send(HTTPStatus.OK, body)

        def log_message(self, format: str, *args) -> None:
            return

    return AdminDemoHandler


def run_admin_demo(database_path: Path, password: str, port: int = 8090) -> None:
    protector = IdentityProtector(Fernet.generate_key(), b"admin-demo-hash-key-" * 2)
    seed_admin_demo(database_path, protector)
    security = AdminDemoSecurity(password)
    handler = create_admin_demo_handler(database_path, protector, security)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Панель: http://127.0.0.1:{port}/admin")
    print(f"Одноразовый пароль: {password}")
    print("Для остановки нажмите Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
