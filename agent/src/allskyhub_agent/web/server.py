"""Local web UI server (SPEC §7): live image, status, focus helper, setup API.

Plain `http.server` in a daemon thread: no extra dependency on the Pi, and the capture loop
never waits for it. Until pairing exists (SPEC §6.2) it is meant for the local network only.

Routes:
    GET  /                    the page
    GET  /api/status          JSON snapshot of the live state
    GET  /api/live.jpg        latest frame, scaled
    GET  /api/crop.jpg        latest frame, 1:1 centre crop (focus mode only)
    GET  /api/setup           device id, hub, pairing code, setup mode (SPEC §6.2, §7, §7.1)
    GET  /api/wifi/networks   visible Wi-Fi networks (setup mode only, SPEC §7.1)
    POST /api/setup/network   join a Wi-Fi network (setup mode only, SPEC §7.1)
    POST /api/focus           body "on" | "off" | "reset"
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from allskyhub_agent.adapters.network import SETUP_ADDRESS
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.settings import AgentSettings, valid_timezone
from allskyhub_agent.setup.controller import NetworkRequest, SetupController

_MAX_BODY = 4096
_SETUP_NET = ipaddress.ip_network(f"{SETUP_ADDRESS}/24", strict=False)


class SetupNetworkBody(BaseModel):
    """`POST /api/setup/network` (SPEC §7.1)."""

    model_config = ConfigDict(extra="forbid")

    ssid: str = Field(min_length=1)
    password: str | None = None
    country: str = Field(pattern=r"^[A-Za-z]{2}$")
    hub_url: str | None = Field(default=None, pattern=r"^https?://")
    # From the phone during onboarding: needed for day and night (SPEC §4.2) and the
    # local night folders (SPEC §4.5).
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None and not valid_timezone(v):
            raise ValueError("unknown time zone")
        return v

    @field_validator("ssid")
    @classmethod
    def _ssid_bytes(cls, v: str) -> str:
        if len(v.encode()) > 32:
            raise ValueError("ssid has more than 32 bytes")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not 8 <= len(v) <= 63:
            raise ValueError("password must have 8 to 63 characters")
        return v


def _page() -> bytes:
    return resources.files("allskyhub_agent.web").joinpath("index.html").read_bytes()


def make_handler(
    live: LiveState,
    pairing: PairingState | None = None,
    setup: SetupController | None = None,
    setup_net: ipaddress.IPv4Network | ipaddress.IPv6Network = _SETUP_NET,
    settings: Callable[[], AgentSettings] | None = None,
) -> type[BaseHTTPRequestHandler]:
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

        def _json(self, status: HTTPStatus, data: object) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _text(self, status: HTTPStatus, text: str) -> None:
            self._send(status, text.encode(), "text/plain; charset=utf-8")

        def _from_setup_network(self) -> bool:
            """Setup endpoints answer only in setup mode and only to clients on its network."""
            if setup is None or not setup.active:
                return False
            try:
                client = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return False
            if client in setup_net:
                setup.touch(time.monotonic())
                return True
            return False

        def _read_body(self) -> bytes | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._text(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
                return None
            return self.rfile.read(length)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif path == "/api/status":
                self._json(HTTPStatus.OK, live.status())
            elif path == "/api/setup":
                if pairing is None:
                    self._text(HTTPStatus.NOT_FOUND, "no hub configured")
                    return
                info = asdict(pairing.info())
                info["setup_mode"] = setup is not None and setup.active
                err = setup.last_error if setup is not None else None
                info["last_error"] = err.value if err is not None else None
                if settings is not None:
                    cur = settings()
                    info["location_set"] = cur.has_location
                    info["timezone"] = cur.timezone
                    info["camera"] = cur.camera
                self._json(HTTPStatus.OK, info)
            elif path == "/api/wifi/networks":
                if setup is None or not self._from_setup_network():
                    self._text(HTTPStatus.FORBIDDEN, "only in setup mode")
                    return
                self._json(HTTPStatus.OK, [asdict(n) for n in setup.networks()])
            elif path in ("/api/live.jpg", "/api/crop.jpg"):
                img = live.live_jpeg() if path == "/api/live.jpg" else live.crop_jpeg()
                if img is None:
                    self._text(HTTPStatus.NOT_FOUND, "no image yet")
                else:
                    self._send(HTTPStatus.OK, img, "image/jpeg")
            else:
                self._text(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/focus":
                self._post_focus()
            elif path == "/api/setup/network":
                self._post_network()
            else:
                self._text(HTTPStatus.NOT_FOUND, "not found")

        def _post_focus(self) -> None:
            raw = self._read_body()
            if raw is None:
                return
            action = raw.decode(errors="replace").strip()
            if action == "on":
                live.set_focus_mode(True)
            elif action == "off":
                live.set_focus_mode(False)
            elif action == "reset":
                live.reset_max()
            else:
                self._text(HTTPStatus.BAD_REQUEST, "expected on, off or reset")
                return
            self._json(HTTPStatus.OK, live.status())

        def _post_network(self) -> None:
            if setup is None or not self._from_setup_network():
                self._text(HTTPStatus.FORBIDDEN, "only in setup mode")
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = SetupNetworkBody.model_validate_json(raw)
            except ValidationError as exc:
                # Field names only: never echo the submitted values (the password).
                fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "invalid", "fields": fields})
                return
            if (body.latitude is None) != (body.longitude is None):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"detail": "invalid", "fields": ["latitude", "longitude"]},
                )
                return
            req = NetworkRequest(
                body.ssid,
                body.password,
                body.country.upper(),
                body.hub_url,
                body.latitude,
                body.longitude,
                body.timezone,
            )
            setup.request_network(req, time.monotonic())
            self._json(HTTPStatus.ACCEPTED, {"will_join": body.ssid})

    return Handler


class WebServer:
    def __init__(
        self,
        live: LiveState,
        host: str,
        port: int,
        pairing: PairingState | None = None,
        setup: SetupController | None = None,
        setup_net: str | None = None,
        settings: Callable[[], AgentSettings] | None = None,
    ) -> None:
        net = ipaddress.ip_network(setup_net) if setup_net else _SETUP_NET
        handler = make_handler(live, pairing, setup, net, settings)
        self._httpd = ThreadingHTTPServer((host, port), handler)
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
