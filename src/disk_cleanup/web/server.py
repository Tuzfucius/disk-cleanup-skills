from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from disk_cleanup.web.api import AuditApi

STATIC_DIR = Path(__file__).resolve().parent / "static"


class AuditServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], api: AuditApi) -> None:
        super().__init__(server_address, AuditRequestHandler)
        self.api = api

    @property
    def url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}/?token={self.api.token}"


def create_server(db_path: Path, scan_id: int, host: str = "127.0.0.1", port: int = 0, token: str | None = None, *, allowed_root: str = "C:\\", run_id: str = "", expires_at: str = "") -> AuditServer:
    api = AuditApi(db_path=db_path, scan_id=scan_id, token=token or secrets.token_urlsafe(24), allowed_root=allowed_root, run_id=run_id, expires_at=expires_at)
    return AuditServer((host, port), api)


def serve_forever(
    db_path: Path,
    scan_id: int,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    allowed_root: str = "C:\\",
    run_id: str = "",
    expires_at: str = "",
    token: str | None = None,
) -> None:
    server = create_server(db_path, scan_id, host, port, token=token, allowed_root=allowed_root, run_id=run_id, expires_at=expires_at)
    print(f"审计界面: {server.url}")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def start_in_thread(server: AuditServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


class AuditRequestHandler(BaseHTTPRequestHandler):
    server: AuditServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and not self.authorized(parsed):
            self.write_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return

        if parsed.path in ("", "/"):
            self.write_static("index.html", "text/html; charset=utf-8")
        elif parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
        elif parsed.path == "/static/app.js":
            self.write_static("app.js", "application/javascript; charset=utf-8")
        elif parsed.path == "/static/styles.css":
            self.write_static("styles.css", "text/css; charset=utf-8")
        elif parsed.path == "/api/session":
            self.write_json(self.server.api.session())
        elif parsed.path == "/api/summary":
            self.write_json(self.server.api.summary())
        elif parsed.path == "/api/tree/children":
            query = parse_qs(parsed.query)
            node_id = parse_optional_int(query.get("node_id", [None])[0])
            limit = parse_optional_int(query.get("limit", ["100"])[0]) or 100
            self.write_json(self.server.api.tree_children(node_id, min(limit, 500)))
        elif parsed.path == "/api/candidates":
            query = parse_qs(parsed.query)
            limit = parse_optional_int(query.get("limit", ["200"])[0]) or 200
            self.write_json(self.server.api.candidates(min(limit, 500)))
        else:
            self.write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        self.write_json({"error": "read-only server"}, HTTPStatus.METHOD_NOT_ALLOWED)

    def authorized(self, parsed) -> bool:
        query = parse_qs(parsed.query)
        token = query.get("token", [""])[0]
        return secrets.compare_digest(token, self.server.api.token)

    def write_static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIR / filename
        if not path.exists():
            self.write_json({"error": "static file not found"}, HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def write_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise CleanupSessionError("请求体过大")
        body = self.rfile.read(length) if length > 0 else b"{}"
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise CleanupSessionError("请求体必须是 JSON 对象")
        return value

    def log_message(self, format: str, *args) -> None:
        return


def parse_optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None
