"""HTTP backend for the local Floor Data Tool."""

from __future__ import annotations

import json
import socket
import sys
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from floor_data_service import FloorDataService


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: str,
    content_type: str = "text/plain; charset=utf-8",
) -> None:
    body = payload.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def handle_http_error(exc: Exception) -> tuple[int, dict[str, Any]]:
    if isinstance(exc, HTTPError):
        body = exc.read().decode("utf-8", errors="ignore")
        return exc.code, {"error": body or exc.reason}
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return HTTPStatus.GATEWAY_TIMEOUT, {"error": "Request timed out"}
    if isinstance(exc, URLError):
        return HTTPStatus.BAD_GATEWAY, {"error": str(exc.reason)}
    return HTTPStatus.INTERNAL_SERVER_ERROR, {
        "error": str(exc),
        "traceback": traceback.format_exc(limit=8),
    }


class FloorDataHandler(BaseHTTPRequestHandler):
    server_version = "FloorDataTool/1.0"
    app = FloorDataService()

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                text_response(self, HTTPStatus.OK, self.app.workspace.html(), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/files":
                json_response(self, HTTPStatus.OK, self.app.workspace.list_files())
                return
            if parsed.path == "/api/local-file":
                data, content_type, filename = self.app.workspace.local_file(parsed.query)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/api/health":
                json_response(self, HTTPStatus.OK, self.app.health())
                return
            text_response(self, HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            status, payload = handle_http_error(exc)
            json_response(self, status, payload)

    def do_POST(self) -> None:
        try:
            body = read_body(self)
            routes = {
                "/api/server/options": self.app.server_options,
                "/api/server/export": self.app.load_campus_export,
                "/api/read-floor-plan": self.app.read_floor_plan,
                "/api/prepare-floor-import": self.app.prepare_floor_import,
                "/api/push-floor-import": self.app.push_floor_import,
                "/api/verify-floor": self.app.verify_floor,
                "/api/make-floor-plan": self.app.make_floor_plan,
                "/api/upload-floor-plan": self.app.upload_floor_plan,
            }
            action = routes.get(self.path.rstrip("/"))
            if action is None:
                text_response(self, HTTPStatus.NOT_FOUND, "Not found")
                return
            json_response(self, HTTPStatus.OK, action(body))
        except Exception as exc:
            status, payload = handle_http_error(exc)
            json_response(self, status, payload)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), format % args))


def serve(*, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), FloorDataHandler)
    url = f"http://{host}:{port}"
    print(f"Floor Data Tool running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
