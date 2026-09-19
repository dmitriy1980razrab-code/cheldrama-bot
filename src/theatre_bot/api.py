from __future__ import annotations

from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from theatre_bot.database import connect, initialize
from theatre_bot.dialog import Reply, answer


MAX_BODY_BYTES = 16_384


def serialize_reply(reply: Reply) -> dict:
    return {
        "text": reply.text,
        "cards": [asdict(card) for card in reply.cards],
    }


def create_handler(database_path: Path, web_root: Path):
    class TheatreBotHandler(BaseHTTPRequestHandler):
        server_version = "TheatreBot/0.1"

        def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
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

            connection = connect(database_path)
            try:
                initialize(connection)
                reply = answer(connection, message.strip())
            finally:
                connection.close()
            self._send_json(HTTPStatus.OK, serialize_reply(reply))

        def log_message(self, format: str, *args) -> None:
            print(f"{self.address_string()} - {format % args}")

    return TheatreBotHandler


def run_server(
    database_path: Path,
    web_root: Path,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    handler = create_handler(database_path, web_root)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Виджет доступен: http://{host}:{port}")
    print("Для остановки нажмите Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

