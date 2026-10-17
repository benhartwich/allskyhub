"""ASCOM Alpaca: safety monitor and observing conditions (SPEC §7.5, roadmap L16)."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from allskyhub_agent.alpaca.safety import SafetyConfig, SafetyMonitor
from allskyhub_agent.alpaca.server import (
    NOT_CONNECTED,
    NOT_IMPLEMENTED,
    VALUE_NOT_SET,
    Alpaca,
    AlpacaServer,
    PortInUse,
)
from allskyhub_protocol import EnvStatus, SkyMetrics

T0 = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)
DEVICE = "k44i7rl6cuaek5yufvr7oh3c5i"


def sky(minute: float, cloud: float | None, sqm: float | None = 20.8) -> SkyMetrics:
    return SkyMetrics(at=T0 + timedelta(minutes=minute), night_id="20261012",
                      cloud_cover=cloud, sqm_mag=sqm if cloud is not None else None,
                      stars=200)  # fmt: skip


def at(minute: float) -> datetime:
    return T0 + timedelta(minutes=minute)


def test_safe_only_after_holding_clear() -> None:
    s = SafetyMonitor(SafetyConfig(cloud_max=0.30, hold=timedelta(minutes=10)))
    assert not s.is_safe(at(0))  # nothing measured
    for m in range(0, 9, 2):
        s.observe(sky(m, 0.05))
    assert not s.is_safe(at(8))  # not yet 10 minutes clear
    s.observe(sky(10, 0.05))
    assert s.is_safe(at(10))
    assert not s.is_safe(at(21))  # the newest measurement is older than 10 minutes


def test_hysteresis() -> None:
    s = SafetyMonitor(SafetyConfig(cloud_max=0.30, hold=timedelta(minutes=10)))
    for m in range(0, 11, 2):
        s.observe(sky(m, 0.05))
    s.observe(sky(12, 0.40))  # one cloudy frame is not enough
    assert s.is_safe(at(12))
    s.observe(sky(14, 0.25))  # below the limit but not 10 points: no change either way
    assert s.is_safe(at(14))
    s.observe(sky(16, 0.35))
    s.observe(sky(18, 0.31))  # two in a row: unsafe
    assert not s.is_safe(at(18))
    for m in range(20, 31, 2):
        s.observe(sky(m, 0.25))  # not clearly below
    assert not s.is_safe(at(30))
    for m in range(32, 43, 2):
        s.observe(sky(m, 0.15))
    assert s.is_safe(at(42))


def test_day_and_lightning_are_unsafe() -> None:
    s = SafetyMonitor(SafetyConfig(hold=timedelta(0)))
    s.observe(sky(0, 0.0))
    assert s.is_safe(at(0))
    s.lightning(at(1))
    assert not s.is_safe(at(5))
    assert s.is_safe(at(9)) is False  # stale by then anyway
    s.observe(sky(40, 0.0))
    assert s.is_safe(at(40))  # 39 minutes after the lightning
    s.observe(sky(42, None))  # dawn: no cloud cover any more
    assert not s.is_safe(at(42))


def ask(
    a: Alpaca, kind: str, member: str, method: str, params: dict[str, str]
) -> tuple[Any, int, str]:
    answer = a.member(kind, member, method, params)
    assert answer is not None
    return answer


@pytest.fixture
def alpaca() -> tuple[Alpaca, SafetyMonitor, list[datetime]]:
    safety = SafetyMonitor(SafetyConfig(hold=timedelta(0)))
    clock = [at(0)]
    return Alpaca(DEVICE, "0.1.0", safety, now=lambda: clock[0]), safety, clock


def test_members(alpaca: tuple[Alpaca, SafetyMonitor, list[datetime]]) -> None:
    a, safety, clock = alpaca
    assert a.name == "allskyhub K44I"
    assert ask(a, "safetymonitor", "issafe", "GET", {})[1:] == (NOT_CONNECTED, "not connected")
    assert ask(a, "safetymonitor", "name", "GET", {}) == ("allskyhub K44I", 0, "")
    assert ask(a, "safetymonitor", "interfaceversion", "GET", {})[0] == 3
    assert ask(a, "observingconditions", "interfaceversion", "GET", {})[0] == 2
    assert ask(a, "safetymonitor", "connected", "PUT", {"connected": "True"}) == (None, 0, "")
    assert ask(a, "safetymonitor", "issafe", "GET", {}) == (False, 0, "")
    safety.observe(sky(0, 0.1))
    assert ask(a, "safetymonitor", "issafe", "GET", {}) == (True, 0, "")
    assert a.member("safetymonitor", "nonsense", "GET", {}) is None  # not a member: HTTP 400
    assert ask(a, "safetymonitor", "commandblind", "PUT", {})[1] == NOT_IMPLEMENTED

    oc = "observingconditions"
    ask(a, oc, "connect", "PUT", {})
    assert ask(a, oc, "cloudcover", "GET", {}) == (10.0, 0, "")
    assert ask(a, oc, "skyquality", "GET", {}) == (20.8, 0, "")
    assert ask(a, oc, "temperature", "GET", {})[1] == NOT_IMPLEMENTED
    clock[0] = at(1)
    assert ask(a, oc, "timesincelastupdate", "GET", {"sensorname": ""}) == (60.0, 0, "")
    assert ask(a, oc, "timesincelastupdate", "GET", {"sensorname": "SkyQuality".lower()})[0] == 60.0
    assert (
        ask(a, oc, "sensordescription", "GET", {"sensorname": "cloudcover"})[0]
        == "Sternzählung der Allsky-Kamera"
    )
    assert ask(a, oc, "averageperiod", "PUT", {"averageperiod": "0"}) == (None, 0, "")
    assert ask(a, oc, "averageperiod", "PUT", {"averageperiod": "5"})[1] == 0x401
    safety.observe(sky(2, None))  # day: no values
    assert ask(a, oc, "cloudcover", "GET", {})[1] == VALUE_NOT_SET
    state: list[dict[str, Any]] = ask(a, oc, "devicestate", "GET", {})[0]
    # Only operational properties (ConformU): no TimeSinceLastUpdate.
    assert [x["Name"] for x in state] == ["TimeStamp"]
    ids = {d["UniqueID"] for d in a.configured()}
    assert len(ids) == 2
    assert ids == {d["UniqueID"] for d in Alpaca(DEVICE, "9", safety).configured()}  # stable


@pytest.fixture
def server(alpaca: tuple[Alpaca, SafetyMonitor, list[datetime]]) -> Iterator[AlpacaServer]:
    srv = AlpacaServer(alpaca[0], 0, discovery_port=0)
    srv.start()
    yield srv
    srv.stop()


def _http(port: int, method: str, path: str, form: dict[str, str] | None = None) -> tuple[int, Any]:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_weather_sensor_members() -> None:
    """SPEC §7.11: with the weather sensor on, temperature, humidity, dew point and pressure
    answer from it; before its first reading they are not set."""
    env: list[EnvStatus] = [EnvStatus(error="not_found")]
    clock = [at(0)]
    a = Alpaca(DEVICE, "0.1.0", SafetyMonitor(), now=lambda: clock[0], weather=lambda: env[0])
    oc = "observingconditions"
    ask(a, oc, "connected", "PUT", {"connected": "true"})
    assert ask(a, oc, "temperature", "GET", {})[1] == VALUE_NOT_SET
    assert ask(a, oc, "windspeed", "GET", {})[1] == NOT_IMPLEMENTED
    env[0] = EnvStatus(at=at(0), temperature_c=8.4, humidity_pct=91.0, pressure_hpa=1012.3,
                       dewpoint_c=7.1)  # fmt: skip
    clock[0] = at(1)
    assert ask(a, oc, "temperature", "GET", {}) == (8.4, 0, "")
    assert ask(a, oc, "humidity", "GET", {})[0] == 91.0
    assert ask(a, oc, "dewpoint", "GET", {})[0] == 7.1
    assert ask(a, oc, "pressure", "GET", {})[0] == 1012.3
    assert ask(a, oc, "timesincelastupdate", "GET", {"sensorname": "Humidity"})[0] == 60.0
    assert ask(a, oc, "sensordescription", "GET", {"sensorname": "dewpoint"})[0] == (
        "BME280 der Kamera"
    )
    names = {d["Name"] for d in ask(a, oc, "devicestate", "GET", {})[0]}
    assert {"Temperature", "Humidity", "DewPoint", "Pressure"} <= names


def test_http(server: AlpacaServer) -> None:
    p = server.port
    status, body = _http(p, "GET", "/management/apiversions?ClientTransactionID=7")
    assert (status, body["Value"], body["ClientTransactionID"]) == (200, [1], 7)
    first = body["ServerTransactionID"]
    status, body = _http(p, "GET", "/management/v1/configureddevices")
    assert [d["DeviceType"] for d in body["Value"]] == ["SafetyMonitor", "ObservingConditions"]
    assert body["ServerTransactionID"] == first + 1
    form = {"Connected": "true", "ClientID": "1", "ClientTransactionID": "x"}
    status, body = _http(p, "PUT", "/api/v1/safetymonitor/0/connected", form)
    assert status == 200
    assert "Value" not in body
    assert (body["ErrorNumber"], body["ClientTransactionID"]) == (0, 0)
    status, body = _http(p, "GET", "/api/v1/safetymonitor/0/issafe")
    assert (status, body["Value"]) == (200, False)
    # Paths are lowercase; ConformU expects 400 for another case.
    assert _http(p, "GET", "/api/v1/SafetyMonitor/0/IsSafe")[0] == 400
    assert _http(p, "GET", "/api/v1/safetymonitor/0/flyaway")[0] == 400
    assert _http(p, "GET", "/api/v1/safetymonitor/1/issafe")[0] == 400
    assert (
        _http(p, "GET", "/api/v1/observingconditions/0/cloudcover")[1]["ErrorNumber"]
        == NOT_CONNECTED
    )


def test_discovery(server: AlpacaServer) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(b"alpacadiscovery1", ("127.0.0.1", server.discovery_port))
        data, _ = sock.recvfrom(1024)
    finally:
        sock.close()
    assert json.loads(data) == {"AlpacaPort": server.port}


def test_port_in_use(
    alpaca: tuple[Alpaca, SafetyMonitor, list[datetime]], server: AlpacaServer
) -> None:
    with pytest.raises(PortInUse):
        AlpacaServer(alpaca[0], server.port, discovery_port=0)


# --- N.I.N.A. relay (SPEC §7.5, roadmap C15) ---------------------------------------------
HISTORY: list[dict[str, Any]] = [
    {"ImageType": "LIGHT", "TargetName": "M 31", "Filter": "L", "ExposureTime": 120.0,
     "HFR": 2.3456, "Stars": 812, "Date": "2026-10-12T22:41:07"},
    {"ImageType": "FLAT", "TargetName": "", "Filter": "L", "ExposureTime": 1.0},
]  # fmt: skip
SEQUENCE: list[dict[str, Any]] = [
    {"Name": "Start", "Status": "FINISHED", "Items": [{"Name": "Cool", "Status": "FINISHED"}]},
    {"Name": "Targets", "Status": "RUNNING", "Items": [
        {"Name": "M 31", "Status": "RUNNING", "Items": [
            {"Name": "Slew", "Status": "FINISHED"}, {"Name": "Expose", "Status": "RUNNING"},
            {"Name": "Dither", "Status": "CREATED"}]}]},
    {"GlobalTriggers": []},
]  # fmt: skip


def test_nina_parsing() -> None:
    from zoneinfo import ZoneInfo

    from allskyhub_agent.alpaca.nina import last_light, sequence_progress

    target, image = last_light(HISTORY, ZoneInfo("Europe/Vienna"))
    assert target == "M 31"
    assert image is not None
    assert (image.filter, image.exposure_s, image.hfr, image.stars) == ("L", 120.0, 2.35, 812)
    assert image.at == datetime(2026, 10, 12, 20, 41, 7, tzinfo=UTC)
    seq = sequence_progress(SEQUENCE)
    assert seq is not None
    assert (seq.name, seq.progress) == ("M 31", 0.5)  # 2 of 4 instructions done
    assert sequence_progress(None) is None
    assert last_light("nonsense", UTC) == (None, None)


def test_nina_relay() -> None:
    import httpx

    from allskyhub_agent.alpaca.nina import NinaRelay

    up = [True]

    def handle(request: httpx.Request) -> httpx.Response:
        if not up[0]:
            raise httpx.ConnectError("down", request=request)
        body = HISTORY if request.url.path.endswith("/image-history") else SEQUENCE
        return httpx.Response(200, json={"Response": body, "Success": True, "StatusCode": 200})

    relay = NinaRelay(
        "http://192.168.1.30:1888",
        UTC,
        safe=lambda: True,
        client=lambda: httpx.Client(
            base_url="http://nina/v2/api", transport=httpx.MockTransport(handle)
        ),
        now=lambda: T0,
    )
    assert relay.latest is None
    relay.poll()
    state = relay.latest
    assert state is not None
    assert (state.connected, state.target, state.safe) == (True, "M 31", True)
    up[0] = False
    relay.poll()
    state = relay.latest
    assert state is not None
    assert (state.connected, state.target) == (False, "M 31")  # the last known state stays


def test_observatory_settings() -> None:
    from allskyhub_agent.profiles import get_profile
    from allskyhub_agent.settings import AgentSettings
    from allskyhub_agent.settings_schema import build_schema, check

    schema = build_schema(get_profile("zwo-asi678mc"))
    assert check(schema, {"alpaca_enabled": True, "safe_cloud_max": 40}) == []
    assert check(schema, {"nina_url": "http://192.168.1.30:1888"}) == []
    assert check(schema, {"nina_url": ""}) == []
    assert check(schema, {"nina_url": "ftp://x"}) == ["nina_url"]
    assert check(schema, {"alpaca_port": 80}) == ["alpaca_port"]
    s = AgentSettings().with_values({"alpaca_enabled": True, "nina_url": " http://n:1888 "})
    assert (s.alpaca_enabled, s.alpaca_port, s.nina_url) == (True, 11111, "http://n:1888")
