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
    GET  /api/nights          nights with frame count, products and events (roadmap L4)
    GET  /api/nights/<n>/frames?offset=&limit=   the night's frame index (SPEC §5.1)
    GET  /api/nights/<n>/events   detections of the night (SPEC §6.4, roadmap L5)
    GET  /api/nights/<n>/sky      sky log of the night (SPEC §5.1, roadmap L6)
    GET  /api/loop            frames of the last hour (roadmap L3)
    GET  /files/<n>/<image>[?thumb=1], /files/<n>/products/<name>[?thumb=1],
         /files/<n>/events/<id>.jpg[?thumb=1]   stored files, with Range for the video
    GET  /api/settings        {schema, values} for the settings form (SPEC §6.9)
    POST /api/settings        partial JSON object → 202, or 400 {detail, fields}
    GET  /api/calibration     sky mask state, drawn areas, orientation overlay (roadmap L8)
    GET  /api/skymask.png?w=&h=   transparent shade over everything that is not sky
    POST /api/calibration/mask      {exclude, include} drawn areas → the new state
    POST /api/calibration/relearn   drop the learned mask → the new state
    GET  /local-login         login page (SPEC §7.4)
    GET  /local-login/start   single sign-on: nonce cookie, redirect to the hub
    GET  /local-login/callback   page that posts the assertion from the fragment
    POST /local-login/callback   assertion → session
    POST /local-login/code    offline code → session
    POST /local-login/logout  end the session

Once the camera is paired, everything except `GET /api/setup` and the login routes needs a
local session (SPEC §7.4).
"""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlencode

import allskyhub_design
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from allskyhub_agent.adapters.network import SETUP_ADDRESS
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.settings import AgentSettings, valid_timezone
from allskyhub_agent.setup.controller import NetworkRequest, SetupController
from allskyhub_agent.web.archive import LocalArchive
from allskyhub_agent.web.auth import (
    NONCE_COOKIE,
    NONCE_TTL_S,
    SESSION_COOKIE,
    SESSION_TTL_S,
    LocalAuth,
)
from allskyhub_agent.web.calibration import LocalCalibration
from allskyhub_protocol import SettingSpec

_MAX_BODY = 4096
_MAX_MASK_BODY = 256 * 1024  # 30 polygons of 200 points as JSON
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


@dataclass(frozen=True)
class SettingsForm:
    """What the local settings form needs (SPEC §6.9, roadmap L7)."""

    schema: Callable[[], list[SettingSpec]]
    values: Callable[[], dict[str, Any]]
    # Checks and stores a partial update; returns the invalid keys (nothing stored then).
    change: Callable[[Mapping[str, Any], str], list[str]]


def _page(name: str = "index.html") -> bytes:
    return resources.files("allskyhub_agent.web").joinpath(name).read_bytes()


_NIGHT_API = re.compile(r"^/api/nights/(\d{8})/(frames|events|sky)$")
_FILE = re.compile(r"^/files/(\d{8})/(?:(products|events)/)?([A-Za-z0-9_.-]+)$")
_TYPES = {".jpg": "image/jpeg", ".mp4": "video/mp4"}
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
# The page's own files and the shared look (packages/design); open like the login page.
_ASSETS = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
_DESIGN = "/static/design/"
_DESIGN_TYPES = {".css": "text/css; charset=utf-8", ".woff2": "font/woff2", ".svg": "image/svg+xml"}
# Routes that answer without a session on a paired camera (SPEC §7.4).
_OPEN = {
    "/api/setup",
    "/local-login",
    "/local-login/start",
    "/local-login/callback",
    "/local-login/code",
    *_ASSETS,
}


def make_handler(
    live: LiveState,
    pairing: PairingState | None = None,
    setup: SetupController | None = None,
    setup_net: ipaddress.IPv4Network | ipaddress.IPv6Network = _SETUP_NET,
    settings: Callable[[], AgentSettings] | None = None,
    auth: LocalAuth | None = None,
    time_trusted: Callable[[], bool] = lambda: True,
    settings_form: SettingsForm | None = None,
    archive: LocalArchive | None = None,
    calibration: LocalCalibration | None = None,
) -> type[BaseHTTPRequestHandler]:
    page = _page()
    login_page = _page("login.html")
    callback_page = _page("callback.html")
    assets = {path: (_page(name), ctype) for path, (name, ctype) in _ASSETS.items()}

    class Handler(BaseHTTPRequestHandler):
        server_version = "allskyhub-agent"

        def log_message(self, format: str, *args: object) -> None:
            return  # keep the journal free of request noise

        def _send(
            self,
            status: HTTPStatus,
            body: bytes,
            ctype: str,
            headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _design(self, name: str) -> None:
            """A file of the shared look (packages/design), the same as the hub serves."""
            try:
                file = allskyhub_design.path(name)
                body = file.read_bytes()
            except (KeyError, OSError):
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            ctype = _DESIGN_TYPES.get(file.suffix, "application/octet-stream")
            self._send(HTTPStatus.OK, body, ctype)

        def _query(self) -> dict[str, str]:
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            return {k: v[0] for k, v in parse_qs(q).items() if v}

        def _int(self, name: str, default: int) -> int:
            try:
                return int(self._query().get(name, default))
            except ValueError:
                return default

        def _send_file(self, path: Path | None) -> None:
            """A stored file; one byte range if asked (browsers seek in the timelapse)."""
            ctype = _TYPES.get(path.suffix) if path is not None else None
            if path is None or ctype is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                f = path.open("rb")
            except OSError:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            with f:
                size = path.stat().st_size
                start, end = 0, size - 1
                m = _RANGE.match(self.headers.get("Range", ""))
                partial = m is not None and size > 0 and (m[1] or m[2])
                if m is not None and partial:
                    if m[1]:
                        start, end = int(m[1]), int(m[2]) if m[2] else size - 1
                    else:
                        start = max(0, size - int(m[2]))
                    end = min(end, size - 1)
                    if start > end:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                # Stored files never change; behind the login they are private.
                self.send_header("Cache-Control", "private, max-age=86400")
                self.end_headers()
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def _archive_get(self, path: str) -> bool:
            """Archive routes (roadmap L3–L6); False if `path` is none of them."""
            if not path.startswith(("/api/nights", "/api/loop", "/files/")):
                return False
            if archive is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return True
            if path == "/api/nights":
                self._json(HTTPStatus.OK, archive.nights())
            elif path == "/api/loop":
                self._json(HTTPStatus.OK, archive.loop(datetime.now(UTC)))
            elif m := _NIGHT_API.match(path):
                night, what = m[1], m[2]
                if what == "frames":
                    data: object = archive.frames(
                        night, self._int("offset", 0), self._int("limit", 2000)
                    )
                elif what == "events":
                    data = archive.events(night)
                else:
                    data = archive.sky(night)
                self._json(HTTPStatus.OK, data)
            elif m := _FILE.match(unquote(path)):
                night, sub, name = m[1], m[2], m[3]
                thumb = self._query().get("thumb") == "1"
                if sub == "products":
                    self._send_file(archive.product(night, name, thumb))
                elif sub == "events":
                    eid = name.removesuffix(".jpg")
                    self._send_file(archive.event_image(night, eid, thumb))
                else:
                    self._send_file(archive.image(night, name, thumb))
            else:
                self._text(HTTPStatus.NOT_FOUND, "not found")
            return True

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

        def _read_body(self, limit: int = _MAX_BODY) -> bytes | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                self._text(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
                return None
            return self.rfile.read(length)

        # --- local login (SPEC §7.4) -------------------------------------------------------
        def _from_loopback(self) -> bool:
            return self.client_address[0] in _LOOPBACK

        def _https(self) -> bool:
            """A reverse proxy on this machine says whether the browser used HTTPS."""
            proto = self.headers.get("X-Forwarded-Proto", "") if self._from_loopback() else ""
            return proto.split(",")[0].strip().lower() == "https"

        def _prefix(self) -> str:
            raw = self.headers.get("X-Forwarded-Prefix", "") if self._from_loopback() else ""
            prefix = "/" + raw.strip().strip("/")
            return prefix if prefix.endswith("/") else prefix + "/"

        def _base(self) -> str:
            """The absolute URL under which the browser reached the local UI."""
            host = self.headers.get("Host") or "localhost"
            scheme = "https" if self._https() else "http"
            return f"{scheme}://{host}{self._prefix()}"

        def _cookie(self, name: str) -> str | None:
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except CookieError:
                return None
            morsel = jar.get(name)
            return morsel.value if morsel is not None else None

        def _set_cookie(self, name: str, value: str, max_age: int) -> tuple[str, str]:
            attrs = [
                f"{name}={value}",
                f"Path={self._prefix()}",
                f"Max-Age={max_age}",
                "HttpOnly",
                "SameSite=Lax",
            ]
            if self._https():
                attrs.append("Secure")
            return ("Set-Cookie", "; ".join(attrs))

        def _redirect(self, location: str, headers: tuple[tuple[str, str], ...] = ()) -> None:
            self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", (("Location", location), *headers))

        def _login_needed(self, path: str) -> bool:
            if auth is None or not auth.paired or path in _OPEN or path.startswith(_DESIGN):
                return False
            return not auth.valid(self._cookie(SESSION_COOKIE))

        def _refuse(self, path: str) -> None:
            if path.startswith("/api/"):
                self._json(HTTPStatus.UNAUTHORIZED, {"detail": "login required"})
            else:
                self._redirect(self._base() + "local-login")

        def _login_start(self) -> None:
            if auth is None or pairing is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            info = pairing.info()
            nonce = auth.new_nonce()
            query = urlencode({"return": self._base(), "nonce": nonce})
            target = f"{info.hub_url.rstrip('/')}/cameras/{info.device_id}/local-login?{query}"
            self._redirect(target, (self._set_cookie(NONCE_COOKIE, nonce, int(NONCE_TTL_S)),))

        def _form(self) -> dict[str, str] | None:
            raw = self._read_body()
            if raw is None:
                return None
            fields = parse_qs(raw.decode(errors="replace"))
            return {k: v[0] for k, v in fields.items() if v}

        def _logged_in(self, token: str) -> None:
            self._redirect(
                self._base(),
                (
                    self._set_cookie(SESSION_COOKIE, token, SESSION_TTL_S),
                    self._set_cookie(NONCE_COOKIE, "", 0),
                ),
            )

        def _login_callback(self) -> None:
            form = self._form()
            if form is None:
                return
            if auth is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            sub = auth.check_assertion(
                form.get("assertion", ""), self._cookie(NONCE_COOKIE), time_trusted()
            )
            if sub is None:
                self._redirect(self._base() + "local-login?e=failed")
                return
            self._logged_in(auth.open_session("hub", sub))

        def _login_code(self) -> None:
            form = self._form()
            if form is None:
                return
            if auth is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            result = auth.check_code(form.get("code", ""))
            if result != "ok":
                self._redirect(self._base() + "local-login?e=" + result)
                return
            self._logged_in(auth.open_session("code"))

        def _logout(self) -> None:
            if auth is not None:
                auth.close_session(self._cookie(SESSION_COOKIE))
            self._redirect(self._base() + "local-login", (self._set_cookie(SESSION_COOKIE, "", 0),))

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if self._login_needed(path):
                self._refuse(path)
                return
            if path in assets:
                body, ctype = assets[path]
                self._send(HTTPStatus.OK, body, ctype)
            elif path.startswith(_DESIGN):
                self._design(path.removeprefix(_DESIGN))
            elif path == "/local-login":
                self._send(HTTPStatus.OK, login_page, "text/html; charset=utf-8")
            elif path == "/local-login/start":
                self._login_start()
            elif path == "/local-login/callback":
                self._send(HTTPStatus.OK, callback_page, "text/html; charset=utf-8")
            elif path == "/":
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif self._archive_get(path):
                pass
            elif path == "/api/settings":
                if settings_form is None:
                    self._text(HTTPStatus.NOT_FOUND, "settings are not kept on this camera")
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "schema": [
                            s.model_dump(mode="json", exclude_none=True)
                            for s in settings_form.schema()
                        ],
                        "values": settings_form.values(),
                    },
                )
            elif path == "/api/calibration":
                if calibration is None:
                    self._text(HTTPStatus.NOT_FOUND, "no camera yet")
                    return
                self._json(HTTPStatus.OK, calibration.info())
            elif path == "/api/skymask.png":
                png = (
                    calibration.shade_png(self._int("w", 960), self._int("h", 540))
                    if calibration is not None
                    else None
                )
                if png is None:
                    self._text(HTTPStatus.NOT_FOUND, "no sky mask yet")
                else:
                    self._send(HTTPStatus.OK, png, "image/png")
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
            if self._login_needed(path):
                self._refuse(path)
                return
            if path == "/local-login/callback":
                self._login_callback()
            elif path == "/local-login/code":
                self._login_code()
            elif path == "/local-login/logout":
                self._logout()
            elif path == "/api/settings":
                self._post_settings()
            elif path == "/api/focus":
                self._post_focus()
            elif path in ("/api/calibration/mask", "/api/calibration/relearn"):
                self._post_calibration(path)
            elif path == "/api/setup/network":
                self._post_network()
            else:
                self._text(HTTPStatus.NOT_FOUND, "not found")

        def _post_settings(self) -> None:
            if settings_form is None:
                self._text(HTTPStatus.NOT_FOUND, "settings are not kept on this camera")
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body: object = json.loads(raw)
            except ValueError:
                body = None
            if not isinstance(body, dict) or not body:
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "invalid", "fields": ["args"]})
                return
            bad = settings_form.change(cast(dict[str, Any], body), "the local UI")
            if bad:
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "invalid", "fields": bad})
                return
            # SPEC §6.5: stored; the capture restarts with the new settings in a moment.
            self._json(HTTPStatus.ACCEPTED, {"restarting": True})

        def _post_calibration(self, path: str) -> None:
            if calibration is None:
                self._text(HTTPStatus.NOT_FOUND, "no camera yet")
                return
            raw = self._read_body(_MAX_MASK_BODY)
            if raw is None:
                return
            if path.endswith("/relearn"):
                calibration.forget_mask()
            else:
                try:
                    body: object = json.loads(raw)
                except ValueError:
                    body = None
                if not calibration.set_edits(body):
                    self._json(HTTPStatus.BAD_REQUEST, {"detail": "invalid", "fields": ["areas"]})
                    return
            self._json(HTTPStatus.OK, calibration.info())

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
        auth: LocalAuth | None = None,
        time_trusted: Callable[[], bool] = lambda: True,
        settings_form: SettingsForm | None = None,
        archive: LocalArchive | None = None,
        calibration: LocalCalibration | None = None,
    ) -> None:
        net = ipaddress.ip_network(setup_net) if setup_net else _SETUP_NET
        handler = make_handler(
            live,
            pairing,
            setup,
            net,
            settings,
            auth,
            time_trusted,
            settings_form,
            archive,
            calibration,
        )
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
