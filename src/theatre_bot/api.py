from __future__ import annotations

from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse
import secrets
from logging import Logger

from theatre_bot.database import connect, initialize
from theatre_bot.dialog import Reply, answer
from theatre_bot.security import ConversationStore, RateLimiter, valid_session_id
from theatre_bot.technical_log import create_technical_logger, record_error


MAX_BODY_BYTES = 16_384


def serialize_reply(reply: Reply) -> dict:
    return {
        "text": reply.text,
        "cards": [asdict(card) for card in reply.cards],
    }


def create_handler(
    database_path: Path,
    web_root: Path,
    conversations: ConversationStore | None = None,
    rate_limiter: RateLimiter | None = None,
    technical_logger: Logger | None = None,
):
    conversation_store = conversations or ConversationStore()
    limiter = rate_limiter or RateLimiter()
    logger = technical_logger or create_technical_logger(database_path.parent / "technical.log")

    class TheatreBotHandler(BaseHTTPRequestHandler):
        server_version = "TheatreBot/0.1"

        def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' https://www.cheldrama.ru data:; connect-src 'self'; "
                "frame-ancestors 'self'",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: HTTPStatus, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return

            files = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/widget.css": ("widget.css", "text/css; charset=utf-8"),
                "/widget.js": ("widget.js", "text/javascript; charset=utf-8"),
            }
            target = files.get(path)
            if target is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            file_name, content_type = target
            try:
                body = (web_root / file_name).read_bytes()
            except FileNotFoundError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "asset_not_found"})
                return
            except OSError as error:
                record_error(logger, "http.static_asset", error)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})
                return
            self._send_bytes(HTTPStatus.OK, body, content_type)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/chat":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid_body_size"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
                return
            message = payload.get("message") if isinstance(payload, dict) else None
            if not isinstance(message, str) or not message.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "message_required"})
                return
            if len(message) > 1000:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "message_too_long"})
                return
            supplied_session_id = payload.get("session_id")
            if supplied_session_id is not None and not valid_session_id(supplied_session_id):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_session_id"})
                return
            session_id = supplied_session_id or secrets.token_urlsafe(24)
            client_ip = self.client_address[0]
            if not limiter.allow(f"{client_ip}:{session_id}"):
                self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limit"})
                return

            connection = None
            try:
                connection = connect(database_path)
                initialize(connection)
                history = tuple(
                    turn.user_text for turn in conversation_store.get(session_id)
                )
                reply = answer(
                    connection,
                    message.strip(),
                    history=history,
                    channel="site",
                )
            except Exception as error:
                record_error(logger, "http.chat", error)
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal_error"},
                )
                return
            finally:
                if connection is not None:
                    connection.close()
            conversation_store.add(session_id, message.strip(), reply.text)
            response = serialize_reply(reply)
            response["session_id"] = session_id
            self._send_json(HTTPStatus.OK, response)

        def log_message(self, format: str, *args) -> None:
            # Не выводим IP-адрес и строку запроса в технический журнал.
            return

    return TheatreBotHandler


def run_server(
    database_path: Path,
    web_root: Path,
    host: str = "127.0.0.1",
    port: int = 8080,
    log_path: Path | None = None,
) -> None:
    technical_logger = create_technical_logger(
        log_path or database_path.parent / "technical.log"
    )
    handler = create_handler(database_path, web_root, technical_logger=technical_logger)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Виджет доступен: http://{host}:{port}")
    print("Для остановки нажмите Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
