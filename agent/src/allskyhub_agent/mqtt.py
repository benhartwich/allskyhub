"""MQTT for home automation (SPEC §7.7, roadmap L23).

A small MQTT 3.1.1 publisher, no extra dependency on the Pi: CONNECT with a last will,
PUBLISH with QoS 0 (retained where the SPEC says so), PINGREQ to keep the connection.
The agent only publishes; it subscribes to nothing. A broker that cannot be reached is
retried with backoff in its own thread and never slows capture.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import socket
import ssl
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from allskyhub_protocol import Event

log = logging.getLogger(__name__)

KEEPALIVE_S = 60
STATE_EVERY_S = 60.0
_CONNECT, _CONNACK, _PUBLISH, _PINGREQ, _DISCONNECT = 0x10, 0x20, 0x30, 0xC0, 0xE0


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int = 1883
    tls: bool = False
    user: str = ""
    password: str | None = None
    prefix: str = "allskyhub"


# --- packets -------------------------------------------------------------------------------


def _length(n: int) -> bytes:
    """MQTT's variable-length "remaining length"."""
    out = bytearray()
    while True:
        n, digit = divmod(n, 128)
        out.append(digit | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str | bytes) -> bytes:
    b = s.encode() if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


def connect_packet(
    client_id: str, will_topic: str, will: bytes, user: str, password: str | None
) -> bytes:
    flags = 0x02 | 0x04 | 0x20  # clean session, will flag, will retain (QoS 0)
    payload = _str(client_id) + _str(will_topic) + _str(will)
    if user:
        flags |= 0x80
        payload += _str(user)
        if password:
            flags |= 0x40
            payload += _str(password)
    var = _str("MQTT") + bytes([4, flags]) + struct.pack("!H", KEEPALIVE_S)
    body = var + payload
    return bytes([_CONNECT]) + _length(len(body)) + body


def publish_packet(topic: str, payload: bytes, retain: bool) -> bytes:
    body = _str(topic) + payload
    return bytes([_PUBLISH | (0x01 if retain else 0)]) + _length(len(body)) + body


# --- what is published ---------------------------------------------------------------------

_SENSORS: list[tuple[str, str, dict[str, str]]] = [
    ("cloud_cover", "Bewölkung", {"unit_of_measurement": "%", "state_class": "measurement"}),
    ("sqm_mag", "Himmelshelligkeit",
     {"unit_of_measurement": "mag/arcsec²", "state_class": "measurement"}),
    ("stars", "Sterne", {"state_class": "measurement"}),
    ("sensor_temp_c", "Sensortemperatur",
     {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}),
    ("cpu_temp_c", "CPU-Temperatur",
     {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}),
    ("exposure_us", "Belichtung", {"unit_of_measurement": "µs", "state_class": "measurement"}),
    ("gain", "Gain", {"state_class": "measurement"}),
    ("mode", "Modus", {}),
]  # fmt: skip
# SPEC §7.11: only with the weather sensor on.
_WEATHER_SENSORS: list[tuple[str, str, dict[str, str]]] = [
    ("env_temp_c", "Lufttemperatur",
     {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}),
    ("humidity_pct", "Luftfeuchte",
     {"unit_of_measurement": "%", "device_class": "humidity", "state_class": "measurement"}),
    ("dewpoint_c", "Taupunkt",
     {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}),
    ("pressure_hpa", "Luftdruck",
     {"unit_of_measurement": "hPa", "device_class": "atmospheric_pressure",
      "state_class": "measurement"}),
]  # fmt: skip


# SPEC §7.12: only with the lightning sensor on.
_LIGHTNING_SENSORS: list[tuple[str, str, dict[str, str]]] = [
    ("strikes_30min", "Blitze (30 min)", {"state_class": "measurement"}),
    ("nearest_km", "Nächster Blitz",
     {"unit_of_measurement": "km", "device_class": "distance", "state_class": "measurement"}),
]  # fmt: skip


def discovery(
    prefix: str,
    device_id: str,
    name: str,
    model: str,
    version: str,
    weather: bool = False,
    heater: bool = False,
    lightning: bool = False,
) -> list[tuple[str, bytes]]:
    """Home Assistant discovery configs (retained), one per value (SPEC §7.7)."""
    base = f"{prefix}/{device_id}"
    device = {
        "identifiers": [f"allskyhub_{device_id}"],
        "name": name,
        "manufacturer": "allskyhub",
        "model": model,
        "sw_version": version,
    }
    common = {
        "state_topic": f"{base}/state",
        "availability_topic": f"{base}/availability",
        "device": device,
    }
    out: list[tuple[str, bytes]] = []
    sensors = _SENSORS + (_WEATHER_SENSORS if weather else [])
    sensors += _LIGHTNING_SENSORS if lightning else []
    for key, label, extra in sensors:
        cfg = {
            "name": label,
            "unique_id": f"allskyhub_{device_id}_{key}",
            "value_template": f"{{{{ value_json.{key} }}}}",
            **common,
            **extra,
        }
        out.append((f"homeassistant/sensor/{device_id}/{key}/config", json.dumps(cfg).encode()))
    safe = {
        "name": "Unsicher (Sternwarte)",
        "unique_id": f"allskyhub_{device_id}_safe",
        "device_class": "safety",  # on = unsafe
        "value_template": "{{ 'OFF' if value_json.safe else 'ON' }}",
        **common,
    }
    out.append((f"homeassistant/binary_sensor/{device_id}/safe/config", json.dumps(safe).encode()))
    if heater:  # SPEC §7.11
        cfg = {
            "name": "Kuppelheizung",
            "unique_id": f"allskyhub_{device_id}_heater",
            "device_class": "heat",
            "value_template": "{{ 'ON' if value_json.heater_on else 'OFF' }}",
            **common,
        }
        out.append((f"homeassistant/binary_sensor/{device_id}/heater/config",
                    json.dumps(cfg).encode()))  # fmt: skip
    return out


def event_payload(e: Event) -> bytes:
    """A detection without images (SPEC §7.7)."""
    data = e.model_dump(mode="json")
    data["at"] = data["start"]
    return json.dumps(data).encode()


# --- the client ----------------------------------------------------------------------------


class MqttPublisher:
    """Publishes the state at most once a minute (and on a mode change) and every event."""

    def __init__(
        self,
        cfg: MqttConfig,
        device_id: str,
        name: str,
        model: str,
        version: str,
        connect: Callable[[MqttConfig], socket.socket] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        weather: bool = False,
        heater: bool = False,
        lightning: bool = False,
    ) -> None:
        self._cfg = cfg
        self._id = device_id
        self._base = f"{cfg.prefix}/{device_id}"
        self._discovery = discovery(
            cfg.prefix, device_id, name, model, version, weather, heater, lightning
        )
        self._connect = connect or _open
        self._monotonic = monotonic
        self._q: queue.Queue[tuple[str, bytes, bool] | None] = queue.Queue(maxsize=200)
        self._last_state: bytes | None = None
        self._last_sent = -1e18
        self._last_mode: object = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mqtt", daemon=True)

    # called from capture and detection threads: never block
    def state(self, values: dict[str, Any]) -> None:
        now = self._monotonic()
        mode = values.get("mode")
        if now - self._last_sent < STATE_EVERY_S and mode == self._last_mode:
            return
        self._last_sent, self._last_mode = now, mode
        payload = json.dumps(values).encode()
        self._last_state = payload
        self._put((f"{self._base}/state", payload, True))

    def event(self, e: Event) -> None:
        self._put((f"{self._base}/event", event_payload(e), False))

    def _put(self, item: tuple[str, bytes, bool]) -> None:
        # The broker is gone for long when it is full: old values are not worth keeping.
        with contextlib.suppress(queue.Full):
            self._q.put_nowait(item)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._q.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        wait = 5.0
        while not self._stop.is_set():
            try:
                self._session()
                wait = 5.0
            except (OSError, ConnectionError) as exc:
                log.warning("MQTT broker %s: %s; again in %.0f s", self._cfg.host, exc, wait)
                if self._stop.wait(wait):
                    return
                wait = min(300.0, wait * 2)

    def _session(self) -> None:
        sock = self._connect(self._cfg)
        try:
            avail = f"{self._base}/availability"
            sock.sendall(
                connect_packet(
                    f"allskyhub-{self._id}"[:23], avail, b"offline", self._cfg.user,
                    self._cfg.password,
                )
            )  # fmt: skip
            ack = _read_exact(sock, 4)
            if ack[0] != _CONNACK or ack[3] != 0:
                raise ConnectionError(f"refused (code {ack[3]})")
            log.info("MQTT connected to %s:%d", self._cfg.host, self._cfg.port)
            sock.sendall(publish_packet(avail, b"online", True))
            for topic, payload in self._discovery:
                sock.sendall(publish_packet(topic, payload, True))
            if self._last_state is not None:
                sock.sendall(publish_packet(f"{self._base}/state", self._last_state, True))
            last_io = self._monotonic()
            while True:  # until the None that stop() queues after everything else
                try:
                    item = self._q.get(timeout=5.0)
                except queue.Empty:
                    item = ()
                if item is None:
                    sock.sendall(publish_packet(avail, b"offline", True))
                    sock.sendall(bytes([_DISCONNECT, 0]))
                    return
                if item:
                    topic, payload, retain = item
                    sock.sendall(publish_packet(topic, payload, retain))
                    last_io = self._monotonic()
                elif self._monotonic() - last_io > KEEPALIVE_S / 2:
                    sock.sendall(bytes([_PINGREQ, 0]))
                    last_io = self._monotonic()
                _drain(sock)
        finally:
            sock.close()


def _open(cfg: MqttConfig) -> socket.socket:
    sock = socket.create_connection((cfg.host, cfg.port), timeout=10)
    if cfg.tls:
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(sock, server_hostname=cfg.host)
    return sock


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed by the broker")
        buf += chunk
    return buf


def _drain(sock: socket.socket) -> None:
    """Read and drop what the broker sent (PINGRESP); a closed socket raises."""
    sock.setblocking(False)
    try:
        while True:
            try:
                data = sock.recv(4096)
            except (BlockingIOError, ssl.SSLWantReadError):
                return
            if not data:
                raise ConnectionError("closed by the broker")
    finally:
        sock.setblocking(True)
        sock.settimeout(10)
