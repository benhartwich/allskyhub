"""Local web UI server (SPEC §7): live image, status and the focus helper.

Plain `http.server` in a daemon thread: no extra dependency on the Pi, and the capture loop
never waits for it. Until pairing exists (SPEC §6.2) it is meant for the local network only.

Routes:
    GET  /                 the page
    GET  /api/status       JSON snapshot of the live state
    GET  /api/live.jpg     latest frame, scaled
    GET  /api/crop.jpg     latest frame, 1:1 centre crop (focus mode only)
    POST /api/focus        body "on" | "off" | "reset"
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from allskyhub_agent.live import LiveState

_MAX_BODY = 64


def _page() -> bytes:
    return resources.files("allskyhub_agent.web").joinpath("index.html").read_bytes()


def make_handler(live: LiveState) -> type[BaseHTTPRequestHandler]:
    page = _page()

    class Handler(BaseHTTPRequestHandler):
        server_version = "allskyhub-agent"

        def log_message(self, format: str, *args: object) -> None:
            return  # keep the journal free of request noise

        def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif path == "/api/status":
                body = json.dumps(live.status()).encode()
                self._send(HTTPStatus.OK, body, "application/json")
            elif path in ("/api/live.jpg", "/api/crop.jpg"):
                img = live.live_jpeg() if path == "/api/live.jpg" else live.crop_jpeg()
                if img is None:
                    self._send(HTTPStatus.NOT_FOUND, b"no image yet", "text/plain")
                else:
                    self._send(HTTPStatus.OK, img, "image/jpeg")
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/api/focus":
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"too large", "text/plain")
                return
            action = self.rfile.read(length).decode(errors="replace").strip()
            if action == "on":
                live.set_focus_mode(True)
            elif action == "off":
                live.set_focus_mode(False)
            elif action == "reset":
                live.reset_max()
            else:
                self._send(HTTPStatus.BAD_REQUEST, b"expected on, off or reset", "text/plain")
                return
            body = json.dumps(live.status()).encode()
            self._send(HTTPStatus.OK, body, "application/json")

    return Handler


class WebServer:
    def __init__(self, live: LiveState, host: str, port: int) -> None:
        self._httpd = ThreadingHTTPServer((host, port), make_handler(live))
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
