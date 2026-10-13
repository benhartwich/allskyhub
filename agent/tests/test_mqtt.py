"""MQTT publisher (SPEC §7.7, roadmap L23) against a fake broker on a socket pair."""

from __future__ import annotations

import json
import socket
import struct
import threading
from datetime import UTC, datetime

from allskyhub_agent.mqtt import (
    MqttConfig,
    MqttPublisher,
    _length,  # pyright: ignore[reportPrivateUsage]
    connect_packet,
    discovery,
    publish_packet,
)
from allskyhub_protocol import Event, EventKind

CFG = MqttConfig("broker.local", user="ben", password="geheim", prefix="sky")  # noqa: S106 - a test value


def _read_packet(sock: socket.socket) -> tuple[int, bytes]:
    head = sock.recv(1)
    if not head:
        raise EOFError
    mult, n = 1, 0
    while True:
        b = sock.recv(1)[0]
        n += (b & 0x7F) * mult
        mult *= 128
        if not b & 0x80:
            break
    body = b""
    while len(body) < n:
        body += sock.recv(n - len(body))
    return head[0], body


def _publish(flags: int, body: bytes) -> tuple[str, bytes, bool]:
    (tl,) = struct.unpack("!H", body[:2])
    return body[2 : 2 + tl].decode(), body[2 + tl :], bool(flags & 1)


def test_length_encoding() -> None:
    assert _length(0) == b"\x00"
    assert _length(127) == b"\x7f"
    assert _length(128) == b"\x80\x01"
    assert _length(16383) == b"\xff\x7f"


def test_connect_packet_has_will_and_login() -> None:
    pkt = connect_packet("cam", "sky/cam/availability", b"offline", "ben", "geheim")
    assert pkt[0] == 0x10
    assert b"MQTT\x04" in pkt
    flags = pkt[pkt.index(b"MQTT\x04") + 5]
    assert flags == 0x02 | 0x04 | 0x20 | 0x80 | 0x40
    assert pkt.endswith(b"\x00\x03ben\x00\x06geheim")
    anon = connect_packet("cam", "t", b"offline", "", None)
    assert anon[anon.index(b"MQTT\x04") + 5] == 0x02 | 0x04 | 0x20
    assert publish_packet("a/b", b"x", True)[0] == 0x31


def test_discovery_names_one_device() -> None:
    configs = dict(discovery("sky", "cam1", "allskyhub cam1", "zwo-asi678mc", "0.1.0"))
    cloud = json.loads(configs["homeassistant/sensor/cam1/cloud_cover/config"])
    assert cloud["state_topic"] == "sky/cam1/state"
    assert cloud["availability_topic"] == "sky/cam1/availability"
    assert cloud["unit_of_measurement"] == "%"
    assert cloud["device"]["identifiers"] == ["allskyhub_cam1"]
    safe = json.loads(configs["homeassistant/binary_sensor/cam1/safe/config"])
    assert safe["device_class"] == "safety"


def test_session_against_a_fake_broker() -> None:
    cam, broker = socket.socketpair()
    clock = [0.0]
    pub = MqttPublisher(CFG, "cam1", "allskyhub cam1", "sim", "0.1.0",
                        connect=lambda _cfg: cam, monotonic=lambda: clock[0])  # fmt: skip
    got: list[tuple[str, bytes, bool]] = []
    connected = threading.Event()

    def serve() -> None:
        kind, _ = _read_packet(broker)
        assert kind == 0x10
        broker.sendall(b"\x20\x02\x00\x00")  # CONNACK, accepted
        connected.set()
        try:
            while True:
                kind, body = _read_packet(broker)
                if kind & 0xF0 == 0x30:
                    got.append(_publish(kind, body))
                elif kind == 0xE0:
                    return
        except (EOFError, OSError):
            return

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    pub.state({"mode": "night", "cloud_cover": 12.5, "safe": True})
    pub.state({"mode": "night", "cloud_cover": 50.0})  # within a minute: dropped
    pub.start()
    assert connected.wait(5)
    clock[0] = 61.0
    pub.state({"mode": "night", "cloud_cover": 20.0})
    at = datetime(2026, 10, 13, 21, 0, tzinfo=UTC)
    pub.event(Event(id="meteor-20261013T210000Z", night_id="20261013", kind=EventKind.METEOR,
                    start=at, end=at, confidence=0.9))  # fmt: skip
    pub.stop()
    server.join(5)
    topics = [t for t, _, _ in got]
    assert topics[0] == "sky/cam1/availability"
    assert got[0][1:] == (b"online", True)
    assert "homeassistant/sensor/cam1/sqm_mag/config" in topics
    states = [json.loads(p) for t, p, r in got if t == "sky/cam1/state" and r]
    # The state queued before the connection, the one a minute later, and the newest
    # state repeated right after connecting; never the one within the minute.
    covers = [s["cloud_cover"] for s in states]
    assert 12.5 in covers
    assert covers[-1] == 20.0
    assert 50.0 not in covers
    event = next(json.loads(p) for t, p, r in got if t == "sky/cam1/event" and not r)
    assert (event["kind"], event["at"]) == ("meteor", "2026-10-13T21:00:00Z")
    assert got[-1] == ("sky/cam1/availability", b"offline", True)
