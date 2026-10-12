"""ASCOM Alpaca server: SafetyMonitor 0 and ObservingConditions 0 (SPEC §7.5).

N.I.N.A., SGP, Voyager and other ASCOM clients find the camera by the Alpaca discovery
broadcast and use it as a safety monitor ("loop while safe") and sky sensor without a
plugin. It only reports; nothing on the camera changes. Plain `http.server` like the
local UI, in its own threads, so capture never waits for it.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import socket
import struct
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from allskyhub_agent.alpaca.safety import SafetyMonitor

log = logging.getLogger(__name__)

DISCOVERY_PORT = 32227
DISCOVERY_GROUP_V6 = "ff12::a1:9aca"
_MAX_BODY = 4096

NOT_IMPLEMENTED = 0x400
INVALID_VALUE = 0x401
VALUE_NOT_SET = 0x402
NOT_CONNECTED = 0x407

_COMMON_INFO = {"description", "driverinfo", "driverversion", "interfaceversion", "name",
                "supportedactions", "connected", "connecting"}  # fmt: skip
_COMMANDS = {"action", "commandblind", "commandbool", "commandstring"}
_OC_NOT_IMPLEMENTED = {"skybrightness", "starfwhm", "dewpoint", "humidity", "pressure",
                       "rainrate", "skytemperature", "temperature", "winddirection",
                       "windgust", "windspeed"}  # fmt: skip
_OC_SENSORS = {
    "cloudcover": "Sternzählung der Allsky-Kamera",
    "skyquality": "Himmelshintergrund, normiert",
}


class PortInUse(Exception):
    """The Alpaca port or UDP 32227 is taken, e.g. by ASCOM Remote (`alpaca_error`)."""


@dataclass
class _Device:
    kind: str  # "safetymonitor" | "observingconditions"
    type_name: str  # "SafetyMonitor" | "ObservingConditions"
    interface_version: int
    unique_id: str
    connected: bool = False


class Alpaca:
    """The two devices and their answers, independent of HTTP (tested directly)."""

    def __init__(
        self,
        device_id: str,
        version: str,
        safety: SafetyMonitor,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.name = f"allskyhub {device_id[:4].upper()}"
        self._version = version
        self._safety = safety
        self._now = now
        self._lock = threading.Lock()
        self._server_tx = 0

        def uid(kind: str) -> str:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"allskyhub:{device_id}:{kind}"))

        self.devices = {
            "safetymonitor": _Device("safetymonitor", "SafetyMonitor", 3, uid("safetymonitor")),
            "observingconditions": _Device(
                "observingconditions", "ObservingConditions", 2, uid("observingconditions")
            ),
        }

    def next_tx(self) -> int:
        with self._lock:
            self._server_tx = (self._server_tx + 1) % 2**32
            return self._server_tx

    # --- management ----------------------------------------------------------------------
    def configured(self) -> list[dict[str, Any]]:
        return [
            {"DeviceName": self.name, "DeviceType": d.type_name, "DeviceNumber": 0,
             "UniqueID": d.unique_id}
            for d in self.devices.values()
        ]  # fmt: skip

    def description(self) -> dict[str, Any]:
        return {"ServerName": "allskyhub", "Manufacturer": "allskyhub",
                "ManufacturerVersion": self._version, "Location": self.name}  # fmt: skip

    # --- device members ------------------------------------------------------------------
    def member(
        self, kind: str, member: str, method: str, params: dict[str, str]
    ) -> tuple[Any, int, str] | None:
        """(Value, ErrorNumber, ErrorMessage); None if `member` is not part of the
        interface for this method (answered with HTTP 400)."""
        d = self.devices[kind]
        put = method == "PUT"
        if member in _COMMANDS and put:
            return None, NOT_IMPLEMENTED, f"{member} is not implemented"
        if put:
            return self._put(d, member, params)
        if member in _COMMON_INFO:
            return self._info(d, member), 0, ""
        if member == "devicestate":
            return (
                self._state(d) if d.connected else None,
                0 if d.connected else NOT_CONNECTED,
                ("" if d.connected else "not connected"),
            )
        if kind == "safetymonitor" and member == "issafe":
            if not d.connected:
                return False, NOT_CONNECTED, "not connected"
            return self._safety.is_safe(self._now()), 0, ""
        if kind == "observingconditions":
            return self._conditions(d, member, params)
        return None

    def _info(self, d: _Device, member: str) -> Any:
        info: dict[str, Any] = {
            "description": f"{self.name} {d.type_name} (allskyhub camera)",
            "driverinfo": "allskyhub agent, ASCOM Alpaca (SPEC §7.5)",
            "driverversion": self._version,
            "interfaceversion": d.interface_version,
            "name": self.name,
            "supportedactions": list[str](),
            "connected": d.connected,
            "connecting": False,
        }
        return info[member]

    def _put(self, d: _Device, member: str, params: dict[str, str]) -> tuple[Any, int, str] | None:
        if member == "connected":
            value = params.get("connected", "").lower()
            if value not in ("true", "false"):
                return None, INVALID_VALUE, "Connected must be true or false"
            d.connected = value == "true"
            return None, 0, ""
        if member == "connect":
            d.connected = True
            return None, 0, ""
        if member == "disconnect":
            d.connected = False
            return None, 0, ""
        if d.kind == "observingconditions" and member == "averageperiod":
            try:
                period = float(params.get("averageperiod", ""))
            except ValueError:
                return None, INVALID_VALUE, "AveragePeriod must be a number"
            if period != 0:
                return None, INVALID_VALUE, "only 0 (no averaging) is supported"
            return None, 0, ""
        if d.kind == "observingconditions" and member == "refresh":
            return None, 0, ""
        return None

    def _conditions(
        self, d: _Device, member: str, params: dict[str, str]
    ) -> tuple[Any, int, str] | None:
        if member in _OC_NOT_IMPLEMENTED:
            return None, NOT_IMPLEMENTED, f"{member} is not measured by this camera"
        if member not in {"cloudcover", "skyquality", "timesincelastupdate",
                          "sensordescription", "averageperiod"}:  # fmt: skip
            return None
        if not d.connected:
            return None, NOT_CONNECTED, "not connected"
        m = self._safety.latest()
        if member == "averageperiod":
            return 0.0, 0, ""
        if member in ("timesincelastupdate", "sensordescription"):
            sensor = params.get("sensorname", "").lower()
            if sensor and sensor not in _OC_SENSORS:
                if sensor in _OC_NOT_IMPLEMENTED:
                    return None, NOT_IMPLEMENTED, f"{sensor} is not measured by this camera"
                return None, INVALID_VALUE, f"unknown sensor {sensor}"
            if member == "sensordescription":
                if not sensor:
                    return None, INVALID_VALUE, "SensorName is required"
                return _OC_SENSORS[sensor], 0, ""
            if m is None:
                return -1.0, 0, ""
            return _age(self._now(), m.at), 0, ""
        value = None
        if m is not None and member == "cloudcover" and m.cloud_cover is not None:
            value = round(100.0 * m.cloud_cover, 1)
        if m is not None and member == "skyquality" and m.sqm_mag is not None:
            value = m.sqm_mag
        if value is None:
            return None, VALUE_NOT_SET, f"no {member} now (day, twilight or no frame yet)"
        return value, 0, ""

    def _state(self, d: _Device) -> list[dict[str, Any]]:
        now = self._now()
        out: list[dict[str, Any]] = []
        if d.kind == "safetymonitor":
            out.append({"Name": "IsSafe", "Value": self._safety.is_safe(now)})
        else:
            m = self._safety.latest()
            if m is not None and m.cloud_cover is not None:
                out.append({"Name": "CloudCover", "Value": round(100.0 * m.cloud_cover, 1)})
            if m is not None and m.sqm_mag is not None:
                out.append({"Name": "SkyQuality", "Value": m.sqm_mag})
            if m is not None:
                out.append({"Name": "TimeSinceLastUpdate",
                            "Value": _age(now, m.at)})  # fmt: skip
        out.append({"Name": "TimeStamp", "Value": now.isoformat().replace("+00:00", "Z")})
        return out


def _age(now: datetime, at: datetime) -> float:
    """Seconds since a measurement; never negative if the clock was set back."""
    return max(0.0, round((now - at).total_seconds(), 1))


def _client_tx(params: dict[str, str]) -> int:
    try:
        v = int(params.get("clienttransactionid", "0"))
    except ValueError:
        return 0
    return v if 0 <= v < 2**32 else 0


def make_handler(alpaca: Alpaca, blocked: Callable[[], bool]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "allskyhub-alpaca"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _params(self, body: bytes) -> dict[str, str]:
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            raw = parse_qs(q) | (parse_qs(body.decode(errors="replace")) if body else {})
            return {k.lower(): v[0] for k, v in raw.items() if v}

        def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reply(self, params: dict[str, str], value: Any, error: int = 0, msg: str = "") -> None:
            # A GET carries Value unless it failed; a PUT never does (Alpaca API).
            out: dict[str, Any] = {"Value": value} if self.command == "GET" and error == 0 else {}
            out |= {
                "ClientTransactionID": _client_tx(params),
                "ServerTransactionID": alpaca.next_tx(),
                "ErrorNumber": error,
                "ErrorMessage": msg,
            }
            self._send(HTTPStatus.OK, json.dumps(out).encode(), "application/json")

        def _bad(self, text: str) -> None:
            self._send(HTTPStatus.BAD_REQUEST, text.encode(), "text/plain; charset=utf-8")

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._bad("body too large")
                return
            body = self.rfile.read(length) if length else b""
            if blocked():
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, b"setup mode", "text/plain")
                return
            params = self._params(body)
            parts = [p for p in self.path.split("?", 1)[0].lower().split("/") if p]
            if parts[:1] == ["management"] and self.command == "GET":
                if parts == ["management", "apiversions"]:
                    self._reply(params, [1])
                    return
                if parts == ["management", "v1", "description"]:
                    self._reply(params, alpaca.description())
                    return
                if parts == ["management", "v1", "configureddevices"]:
                    self._reply(params, alpaca.configured())
                    return
            if len(parts) == 5 and parts[:2] == ["api", "v1"] and parts[2] in alpaca.devices:
                if parts[3] != "0":
                    self._bad(f"no {parts[2]} number {parts[3]}")
                    return
                answer = alpaca.member(parts[2], parts[4], self.command, params)
                if answer is None:
                    self._bad(f"{parts[4]} is not a {self.command} member of {parts[2]}")
                    return
                self._reply(params, *answer)
                return
            self._bad("unknown path")

        def do_GET(self) -> None:
            self._handle()

        def do_PUT(self) -> None:
            self._handle()

    return Handler


class AlpacaServer:
    """HTTP on `port` and the discovery responder; PortInUse if either is taken."""

    def __init__(
        self,
        alpaca: Alpaca,
        port: int,
        blocked: Callable[[], bool] = lambda: False,
        discovery_port: int = DISCOVERY_PORT,
    ) -> None:
        self._port = port
        self._blocked = blocked
        self._stop = threading.Event()
        try:
            self._httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(alpaca, blocked))  # noqa: S104 - LAN service (SPEC §7.5)
        except OSError as exc:
            raise PortInUse(str(exc)) from None
        self._httpd.daemon_threads = True
        try:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp.bind(("0.0.0.0", discovery_port))  # noqa: S104 - LAN discovery
        except OSError as exc:
            self._httpd.server_close()
            raise PortInUse(str(exc)) from None
        self._udp.settimeout(1.0)
        self._udp6 = _ipv6_discovery_socket(discovery_port)

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def discovery_port(self) -> int:
        return int(self._udp.getsockname()[1])

    def start(self) -> None:
        threading.Thread(target=self._httpd.serve_forever, name="alpaca", daemon=True).start()
        for sock in (self._udp, self._udp6):
            if sock is not None:
                threading.Thread(target=self._discovery, args=(sock,), name="alpaca-discovery",
                                 daemon=True).start()  # fmt: skip
        log.info("ASCOM Alpaca on port %d", self.port)

    def _discovery(self, sock: socket.socket) -> None:
        reply = json.dumps({"AlpacaPort": self.port}).encode()
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(1024)
            except TimeoutError:
                continue
            except OSError:
                return
            if data.startswith(b"alpacadiscovery1") and not self._blocked():
                with contextlib.suppress(OSError):
                    sock.sendto(reply, addr)

    def stop(self) -> None:
        self._stop.set()
        self._httpd.shutdown()
        self._httpd.server_close()
        for sock in (self._udp, self._udp6):
            if sock is not None:
                sock.close()


def _ipv6_discovery_socket(port: int) -> socket.socket | None:
    """Discovery over IPv6 multicast, where the system has IPv6; None otherwise."""
    if not socket.has_ipv6:
        return None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind(("::", port))
        group = ipaddress.IPv6Address(DISCOVERY_GROUP_V6).packed
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, group + struct.pack("@I", 0))
        sock.settimeout(1.0)
    except OSError:
        return None
    return sock
