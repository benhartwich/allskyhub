"""Local web UI server (SPEC §7): live image, status, focus helper, setup API.

Plain `http.server` in a daemon thread: no extra dependency on the Pi, and the capture loop
never waits for it. Until pairing exists (SPEC §6.2) it is meant for the local network only.

Routes:
    GET  /                    the page
    GET  /api/status          JSON snapshot of the live state
    GET  /api/health          {version, frames, frame: {mode, captured_at}}, open: the
                              updater's health check and the watchdog (SPEC §8)
    GET  /api/live.jpg        latest frame, scaled
    GET  /api/crop.jpg        latest frame, 1:1 centre crop (focus mode only)
    GET  /api/setup           device id, hub, pairing code, setup mode (SPEC §6.2, §7, §7.1)
    GET  /api/wifi/networks   visible Wi-Fi networks (setup mode only, SPEC §7.1)
    POST /api/setup/network   join a Wi-Fi network (setup mode only, SPEC §7.1)
    POST /api/focus           body "on" | "off" | "reset"
    GET  /api/nights          nights with frame count, products and events (roadmap L4)
    GET  /api/nights/<n>/frames?offset=&limit=&bad=&at=HH:MM   the night's frame index (SPEC §5.1)
    GET  /api/nights/<n>/events   detections of the night (SPEC §6.4, roadmap L5)
    GET  /api/nights/<n>/sky      sky log of the night (SPEC §5.1, roadmap L6)
    GET  /api/loop            frames of the last hour (roadmap L3)
    GET  /api/mini.mp4        the last hour as a small video, if switched on (SPEC §5.2)
    GET  /api/keolapse.jpg?from=&to=   keograms of a range of nights (SPEC §5.4)
    GET  /files/<n>/<image>[?thumb=1], /files/<n>/products/<name>[?thumb=1],
         /files/<n>/events/<id>.jpg[?thumb=1]   stored files, with Range for the video
    GET  /api/settings        {schema, values} for the settings form (SPEC §6.9)
    POST /api/settings        partial JSON object → 202, or 400 {detail, fields}
    GET  /api/calibration     sky mask state, drawn areas, orientation overlay (roadmap L8)
    GET  /api/skymask.png?w=&h=   transparent shade over everything that is not sky
    GET  /api/sky-overlay?at=     constellations and N/O/S/W for an image (roadmap L11)
    POST /api/calibration/mask      {exclude, include} drawn areas → the new state
    POST /api/calibration/relearn   drop the learned mask → the new state
    GET  /api/public          the public page on the hub {enabled, url, profile, online}
                              (SPEC §6.8, roadmap L10); the last known state with
                              online false while the hub is unreachable, else 503
    GET  /api/network         interfaces, addresses, joined Wi-Fi, managed (roadmap L13)
    GET  /api/network/wifi    visible Wi-Fi networks, only where the agent manages the network
    POST /api/network/wifi    {ssid, password?, country} → 202 {will_join}; as in setup mode,
                              a failed join opens the setup network (SPEC §7.1)
    GET  /api/tls             HTTPS on/off, port, whose certificate, fingerprint (SPEC §7.6)
    POST /api/nights/<n>/events/<id>/label   {label, reason} (SPEC §6.4 Labels)
    POST /api/nights/<n>/delete   the whole night, not the current one → 200, 409, 404
    POST /api/capture/pause, /api/capture/resume   (SPEC §4.1) → {paused}
    POST /api/tls             {cert, key} PEM → 202 and a restart, or 400 {detail, fields}
    POST /api/tls/remove      back to the self-signed certificate → 202 and a restart
    GET  /api/update          version, installed releases, the updater's state (roadmap L14)
    POST /api/update/<now|rollback>   ask the root updater → 202, or 404 without one
    GET  /api/logs?lines=N    the agent's recent log lines (roadmap L15) and their `seq`;
                              ?after=<seq> only the newer ones, to follow the log
    GET  /api/overlay, POST /api/overlay   the overlay layout (SPEC §7 Overlay)
    GET  /api/check           the camera check: [{id, state ok|warn|fail, detail}] (SPEC §7)
    GET  /api/support.zip     support bundle: log, versions, facts, settings, calibration
    GET  /api/backup.json     settings and drawn sky-mask areas
    POST /api/restore         a backup → 202 (the capture restarts), or 400 {detail, fields}
    GET  /api/system          CPU, memory, disk, power warnings, uptime (roadmap L12)
    POST /api/system/<action> restart (the agent), reboot, shutdown → 202, or 503 {detail}
    POST /api/public          {enabled?, profile?} → the new state, 422 {detail} with the
                              hub's sentence, 503 while the hub is unreachable
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
import ssl
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
from allskyhub_agent.hub.public import HubUnreachable, PublicPage, PublicRejected
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
from allskyhub_agent.web.check import CheckFacts, run_checks
from allskyhub_agent.web.overlay import OverlayLayout
from allskyhub_agent.web.support import Support
from allskyhub_agent.web.tls import Certificates
from allskyhub_protocol import SettingSpec

_MAX_BODY = 4096
_MAX_MASK_BODY = 256 * 1024  # 30 polygons of 200 points as JSON
_MAX_PUBLIC_BODY = 16 * 1024  # the profile's texts are at most about 3 000 characters
_SETUP_NET = ipaddress.ip_network(f"{SETUP_ADDRESS}/24", strict=False)


def _ssid_bytes(v: str) -> str:
    if len(v.encode()) > 32:
        raise ValueError("ssid has more than 32 bytes")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError("ssid has control characters")
    return v


def _wpa_password(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not 8 <= len(v) <= 63:
        raise ValueError("password must have 8 to 63 characters")
    # WPA passphrases are printable ASCII; a line break would add lines to nmcli's
    # passwd-file (adapters/network.py).
    if not all(32 <= ord(ch) <= 126 for ch in v):
        raise ValueError("password must be printable ASCII")
    return v


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

    _ssid = field_validator("ssid")(_ssid_bytes)
    _password = field_validator("password")(_wpa_password)


_NOT_MANAGED = "Das Netzwerk wird auf dieser Installation nicht von der Kamera verwaltet."


class WifiBody(BaseModel):
    """`POST /api/network/wifi` (roadmap L13): only the Wi-Fi, never hub or location."""

    model_config = ConfigDict(extra="forbid")

    ssid: str = Field(min_length=1)
    password: str | None = None
    country: str = Field(pattern=r"^[A-Za-z]{2}$")

    _ssid = field_validator("ssid")(_ssid_bytes)
    _password = field_validator("password")(_wpa_password)


@dataclass(frozen=True)
class UpdatePage:
    """Roadmap L14: what is installed, and asking the updater (SPEC §8)."""

    facts: Callable[[], dict[str, Any]]
    # Leaves "now" or "rollback" for the updater; False where there is no updater.
    request: Callable[[str], bool]


@dataclass(frozen=True)
class SystemPage:
    """The local UI's system page (roadmap L12)."""

    facts: Callable[[], dict[str, Any]]
    # Starts "restart", "reboot" or "shutdown"; None when under way, else a German reason.
    action: Callable[[str], str | None]


@dataclass(frozen=True)
class SettingsForm:
    """What the local settings form needs (SPEC §6.9, roadmap L7)."""

    schema: Callable[[], list[SettingSpec]]
    values: Callable[[], dict[str, Any]]
    # Checks and stores a partial update; returns the invalid keys (nothing stored then).
    change: Callable[[Mapping[str, Any], str], list[str]]


def _page(name: str = "index.html") -> bytes:
    return resources.files("allskyhub_agent.web").joinpath(name).read_bytes()


_SYSTEM_ACTION = re.compile(r"^/api/system/(restart|reboot|shutdown)$")
_NIGHT_API = re.compile(r"^/api/nights/(\d{8})/(frames|events|sky)$")
_DELETE_NIGHT = re.compile(r"^/api/nights/(\d{8})/delete$")
_LABEL = re.compile(r"^/api/nights/(\d{8})/events/([a-z]+-\d{8}T\d{6}Z(?:-\d+)?)/label$")
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
    "/api/health",
    "/local-login",
    "/local-login/start",
    "/local-login/callback",
    "/local-login/code",
    *_ASSETS,
}


@dataclass(frozen=True)
class TlsSetup:
    """HTTPS for the local UI (SPEC §7.6): the certificates, whether HTTPS is on, its
    port, and what to do after a certificate change (restart the agent)."""

    certs: Certificates
    enabled: bool
    port: int = 8443
    on_change: Callable[[], None] = lambda: None


# Plain HTTP stays usable for these with HTTPS on (SPEC §7.6): the app's onboarding, the
# updater's and the watchdog's health check.
_PLAIN_OK = ("/api/setup", "/api/health")


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
    public: PublicPage | None = None,
    system_page: SystemPage | None = None,
    network_info: Callable[[], dict[str, Any]] | None = None,
    update_page: UpdatePage | None = None,
    support: Support | None = None,
    tls: TlsSetup | None = None,
    check: CheckFacts | None = None,
    overlay: OverlayLayout | None = None,
    status_extra: Callable[[], dict[str, Any]] | None = None,
) -> type[BaseHTTPRequestHandler]:
    page = _page()
    login_page = _page("login.html")
    callback_page = _page("callback.html")
    assets = {path: (_page(name), ctype) for path, (name, ctype) in _ASSETS.items()}

    class Handler(BaseHTTPRequestHandler):
        # Set on the plain-HTTP server's handler while HTTPS is on (SPEC §7.6).
        redirect_port: int | None = None

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
            # No page of the camera may be framed by another site (clickjacking).
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
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
            if not path.startswith(
                ("/api/nights", "/api/loop", "/files/", "/api/keolapse.jpg", "/api/mini.mp4")
            ):
                return False
            if archive is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return True
            if path == "/api/nights":
                self._json(HTTPStatus.OK, archive.nights())
            elif path == "/api/loop":
                self._json(HTTPStatus.OK, archive.loop(datetime.now(UTC)))
            elif path == "/api/mini.mp4":
                self._send_file(archive.mini())
            elif path == "/api/keolapse.jpg":
                q = self._query()
                img = archive.keolapse(q.get("from", ""), q.get("to", ""))
                if img is None:
                    self._text(HTTPStatus.NOT_FOUND, "no nights")
                else:
                    self._send(HTTPStatus.OK, img, "image/jpeg")
            elif m := _NIGHT_API.match(path):
                night, what = m[1], m[2]
                if what == "frames":
                    data: object = archive.frames(
                        night,
                        self._int("offset", 0),
                        self._int("limit", 2000),
                        bad=self._query().get("bad") == "1",
                        at=self._query().get("at"),
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
            """Served over HTTPS (SPEC §7.6), or a reverse proxy on this machine says the
            browser used HTTPS."""
            if isinstance(self.connection, ssl.SSLSocket):
                return True
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

        def _to_https(self, path: str) -> bool:
            """With HTTPS on, send a browser on plain HTTP over (SPEC §7.6); True if sent."""
            port = self.redirect_port
            if port is None or path in _PLAIN_OK:
                return False
            if self._from_loopback() and self.headers.get("X-Forwarded-Proto"):
                return False  # a reverse proxy on this machine (it does its own TLS)
            try:
                if ipaddress.ip_address(self.client_address[0]) in setup_net:
                    return False  # setup mode (SPEC §7.1) stays plain
            except ValueError:
                pass
            host = self.headers.get("Host", "") or SETUP_ADDRESS
            host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            if not re.fullmatch(r"[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\]", host):
                host = SETUP_ADDRESS
            self._send(HTTPStatus.PERMANENT_REDIRECT, b"", "text/plain",
                       (("Location", f"https://{host}:{port}{self.path}"),))  # fmt: skip
            return True

        def _post_delete_night(self, night: str) -> None:
            """SPEC §7: delete a whole night (not the current one), and tell the hub."""
            if self._read_body() is None:
                return
            if archive is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            why = archive.delete_night(night, datetime.now(UTC))
            if why == "current":
                self._json(HTTPStatus.CONFLICT, {"detail": "the current night"})
            elif why is not None:
                self._json(HTTPStatus.NOT_FOUND, {"detail": "no such night"})
            else:
                self._json(HTTPStatus.OK, {"deleted": night})

        def _post_label(self, night: str, event_id: str) -> None:
            """SPEC §6.4 Labels: {label, reason} for one detection, sent on to the hub."""
            raw = self._read_body()
            if raw is None:
                return
            if archive is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                body: object = json.loads(raw or b"{}")
                label = archive.set_label(night, event_id, body)
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "label", "fields": ["label"]})
                return
            if label is None:
                self._json(HTTPStatus.NOT_FOUND, {"detail": "no such event"})
                return
            self._json(HTTPStatus.OK, label.model_dump(mode="json"))

        def _post_tls(self, path: str) -> None:
            """SPEC §7.6: install the owner's certificate and key (PEM), or drop them."""
            raw = self._read_body()
            if raw is None:
                return
            if tls is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            if path == "/api/tls/remove":
                tls.certs.remove_own()
            else:
                try:
                    body: object = json.loads(raw or b"{}")
                except ValueError:
                    body = None
                cert = body.get("cert") if isinstance(body, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                key = body.get("key") if isinstance(body, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                if not isinstance(cert, str) or not isinstance(key, str):
                    self._json(
                        HTTPStatus.BAD_REQUEST, {"detail": "cert and key", "fields": ["tls"]}
                    )
                    return
                why = tls.certs.install_own(cert.encode(), key.encode(), datetime.now(UTC))
                if why is not None:
                    self._json(HTTPStatus.BAD_REQUEST, {"detail": why, "fields": ["tls"]})
                    return
            self._json(HTTPStatus.ACCEPTED, {"restart": True})
            tls.on_change()

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
            if self._to_https(path):
                return
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
            elif path == "/api/public":
                self._public(None)
            elif path == "/api/network":
                if network_info is None:
                    self._text(HTTPStatus.NOT_FOUND, "not found")
                    return
                self._json(HTTPStatus.OK, network_info() | {"managed": setup is not None})
            elif path == "/api/network/wifi":
                if setup is None:
                    self._json(HTTPStatus.NOT_FOUND, {"detail": _NOT_MANAGED})
                    return
                self._json(HTTPStatus.OK, [asdict(n) for n in setup.networks()])
            elif path in ("/api/logs", "/api/support.zip", "/api/backup.json"):
                self._support_get(path)
            elif path == "/api/check":
                if check is None:
                    self._text(HTTPStatus.NOT_FOUND, "not found")
                    return
                self._json(HTTPStatus.OK, run_checks(check, datetime.now(UTC)))
            elif path == "/api/tls":
                if tls is None:
                    self._text(HTTPStatus.NOT_FOUND, "not found")
                    return
                info = tls.certs.info(datetime.now(UTC))
                self._json(HTTPStatus.OK, {"enabled": tls.enabled, "port": tls.port, **info})
            elif path == "/api/update":
                if update_page is None:
                    self._text(HTTPStatus.NOT_FOUND, "not found")
                    return
                self._json(HTTPStatus.OK, update_page.facts())
            elif path == "/api/system":
                if system_page is None:
                    self._text(HTTPStatus.NOT_FOUND, "not found")
                    return
                self._json(HTTPStatus.OK, system_page.facts())
            elif path == "/api/calibration":
                if calibration is None:
                    self._text(HTTPStatus.NOT_FOUND, "no camera yet")
                    return
                self._json(HTTPStatus.OK, calibration.info())
            elif path == "/api/sky-overlay":
                raw_at = self._query().get("at", "")
                try:
                    at = (
                        datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
                        if raw_at
                        else datetime.now(UTC)
                    )
                except ValueError:
                    at = None
                ov = (
                    calibration.sky_overlay(at, calibration.location())
                    if calibration is not None and at is not None and at.tzinfo is not None
                    else None
                )
                if ov is None:
                    self._text(HTTPStatus.NOT_FOUND, "no orientation or location")
                else:
                    self._json(HTTPStatus.OK, ov)
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
                # Plus what the overlay draws besides the frame: sky values and the moon.
                extra = status_extra() if status_extra is not None else {}
                self._json(HTTPStatus.OK, live.status() | extra)
            elif path == "/api/overlay":
                if overlay is None:
                    self._text(HTTPStatus.NOT_FOUND, "not found")
                    return
                self._json(HTTPStatus.OK, overlay.get())
            elif path == "/api/health":
                self._json(HTTPStatus.OK, live.health())
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

        def _cross_site(self) -> bool:
            """A browser POST from another site (CSRF): its Origin is not the address the
            camera was reached under. Without Origin (the app, curl) it is no browser form."""
            origin = self.headers.get("Origin")
            if origin is None:
                return False
            host = self.headers.get("Host", "")
            if self._from_loopback():  # behind the local reverse proxy (SPEC §7.6)
                host = self.headers.get("X-Forwarded-Host", host).split(",")[0].strip()
            _, sep, rest = origin.partition("://")
            return not sep or rest.rstrip("/").lower() != host.lower()

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if self._to_https(path):
                return
            if self._cross_site():
                self._json(HTTPStatus.FORBIDDEN, {"detail": "cross-site request"})
                return
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
            elif path == "/api/network/wifi":
                self._post_wifi()
            elif path == "/api/restore":
                self._restore()
            elif m := _LABEL.match(path):
                self._post_label(m[1], m[2])
            elif m := _DELETE_NIGHT.match(path):
                self._post_delete_night(m[1])
            elif path == "/api/overlay":
                raw = self._read_body()
                if raw is None:
                    return
                try:
                    body: object = json.loads(raw or b"{}")
                except ValueError:
                    body = None
                saved = overlay.set(body) if overlay is not None else None
                if saved is None:
                    self._json(HTTPStatus.BAD_REQUEST, {"detail": "layout", "fields": ["overlay"]})
                else:
                    self._json(HTTPStatus.OK, saved)
            elif path in ("/api/capture/pause", "/api/capture/resume"):
                if self._read_body() is None:
                    return
                live.set_paused(path.endswith("pause"))
                self._json(HTTPStatus.OK, {"paused": live.paused})
            elif path in ("/api/tls", "/api/tls/remove"):
                self._post_tls(path)
            elif path in ("/api/update/now", "/api/update/rollback"):
                if self._read_body() is None:
                    return
                word = path.rsplit("/", 1)[1]
                if update_page is None or not update_page.request(word):
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"detail": "Auf dieser Installation gibt es keinen Updater."},
                    )
                    return
                self._json(HTTPStatus.ACCEPTED, {"requested": word})
            elif m := _SYSTEM_ACTION.match(path):
                if system_page is None:
                    self._text(HTTPStatus.NOT_FOUND, "not found")
                    return
                if self._read_body() is None:
                    return
                why = system_page.action(m[1])
                if why is not None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"detail": why})
                    return
                self._json(HTTPStatus.ACCEPTED, {"action": m[1]})
            elif path == "/api/public":
                raw = self._read_body(_MAX_PUBLIC_BODY)
                if raw is None:
                    return
                try:
                    body: object = json.loads(raw)
                except ValueError:
                    body = None
                if not isinstance(body, dict):
                    self._json(HTTPStatus.BAD_REQUEST, {"detail": "Ungültige Eingabe."})
                    return
                self._public(cast(dict[str, Any], body))
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

        def _public(self, change: dict[str, Any] | None) -> None:
            """SPEC §6.8 through the device API; GET without `change`."""
            unreachable = {"detail": "Der Hub ist gerade nicht erreichbar."}
            if public is None:
                self._json(HTTPStatus.NOT_FOUND, {"detail": "Diese Kamera hat keinen Hub."})
                return
            try:
                state = public.get() if change is None else public.put(change)
            except PublicRejected as exc:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": exc.detail})
                return
            except HubUnreachable:
                if change is None and public.last is not None:
                    self._json(
                        HTTPStatus.OK, public.last.model_dump(mode="json") | {"online": False}
                    )
                else:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, unreachable)
                return
            self._json(HTTPStatus.OK, state.model_dump(mode="json") | {"online": True})

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

        def _support_get(self, path: str) -> None:
            if support is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            if path == "/api/logs":
                after = self._query().get("after")
                if after is not None and after.isdigit():
                    # Following the log: only what came since `after` (the last `seq`).
                    seq, lines = support.log.since(int(after))
                    self._json(HTTPStatus.OK, {"lines": lines, "seq": seq})
                    return
                n = max(1, min(self._int("lines", 300), 2000))
                seq, _ = support.log.since(0)
                self._json(HTTPStatus.OK, {"lines": support.log.lines(n), "seq": seq})
            elif path == "/api/support.zip":
                save = (
                    (
                        "Content-Disposition",
                        f'attachment; filename="allskyhub-support-{stamp}.zip"',
                    ),
                )
                self._send(HTTPStatus.OK, support.bundle(), "application/zip", save)
            else:
                save = (
                    (
                        "Content-Disposition",
                        f'attachment; filename="allskyhub-backup-{stamp}.json"',
                    ),
                )
                body = json.dumps(support.backup(), indent=2).encode()
                self._send(HTTPStatus.OK, body, "application/json", save)

        def _restore(self) -> None:
            if support is None:
                self._text(HTTPStatus.NOT_FOUND, "not found")
                return
            raw = self._read_body(_MAX_MASK_BODY)
            if raw is None:
                return
            try:
                body: object = json.loads(raw)
            except ValueError:
                body = None
            bad = support.apply(body)
            if bad:
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "invalid", "fields": bad})
                return
            self._json(HTTPStatus.ACCEPTED, {"restarting": True})

        def _post_wifi(self) -> None:
            """Roadmap L13: another Wi-Fi from the normal network, like setup mode's join
            but never another hub or location (those stay with the settings)."""
            if setup is None:
                self._json(HTTPStatus.NOT_FOUND, {"detail": _NOT_MANAGED})
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = WifiBody.model_validate_json(raw)
            except ValidationError as exc:
                fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
                self._json(HTTPStatus.BAD_REQUEST, {"detail": "invalid", "fields": fields})
                return
            req = NetworkRequest(body.ssid, body.password, body.country.upper(), None)
            setup.request_network(req, time.monotonic())
            self._json(HTTPStatus.ACCEPTED, {"will_join": body.ssid})

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
        public: PublicPage | None = None,
        system_page: SystemPage | None = None,
        network_info: Callable[[], dict[str, Any]] | None = None,
        update_page: UpdatePage | None = None,
        support: Support | None = None,
        tls: TlsSetup | None = None,
        tls_context: ssl.SSLContext | None = None,
        check: CheckFacts | None = None,
        overlay: OverlayLayout | None = None,
        status_extra: Callable[[], dict[str, Any]] | None = None,
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
            public,
            system_page,
            network_info,
            update_page,
            support,
            tls,
            check,
            overlay,
            status_extra,
        )
        self._tls_httpd: ThreadingHTTPServer | None = None
        if tls is not None and tls.enabled and tls_context is not None:
            # SPEC §7.6: the same pages over HTTPS; plain HTTP redirects there.
            secure = type("SecureHandler", (handler,), {})
            self._tls_httpd = ThreadingHTTPServer((host, tls.port), secure)
            self._tls_httpd.daemon_threads = True
            self._tls_httpd.socket = tls_context.wrap_socket(
                self._tls_httpd.socket, server_side=True
            )
            bound = int(self._tls_httpd.server_address[1])
            handler = type("PlainHandler", (handler,), {"redirect_port": bound})
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def tls_port(self) -> int | None:
        return int(self._tls_httpd.server_address[1]) if self._tls_httpd is not None else None

    def start(self) -> None:
        self._thread.start()
        if self._tls_httpd is not None:
            threading.Thread(target=self._tls_httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._tls_httpd is not None:
            self._tls_httpd.shutdown()
            self._tls_httpd.server_close()
