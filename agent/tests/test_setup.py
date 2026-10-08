"""Setup mode, Wi-Fi handover, setup file, discovery, settings (SPEC §7.1-7.3)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from allskyhub_agent.adapters.network import JoinError, SimNetwork, WifiNetwork, parse_scan
from allskyhub_agent.discovery import SERVICE_TYPE, host_name, service_info
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.settings import DEFAULT_HUB, AgentSettings
from allskyhub_agent.setup.controller import (
    IDLE_S,
    UNREACHABLE_S,
    NetworkRequest,
    SetupController,
    setup_ssid,
)
from allskyhub_agent.setup.setup_file import SetupFileError, apply_once, parse
from allskyhub_agent.web.server import WebServer

DEVICE = "abcd" + "e" * 22


def test_parse_scan_handles_escaped_colons_duplicates_and_hidden() -> None:
    out = "Home:70:WPA2\nHome:40:WPA2\n:90:WPA2\nCafe\\:Bar:55:--\nbad line\nX:notanumber:WPA\n"
    assert parse_scan(out) == [
        WifiNetwork("Home", 70, True),
        WifiNetwork("Cafe:Bar", 55, False),
    ]


def make(net: SimNetwork) -> tuple[SetupController, list[str], list[float]]:
    urls: list[str] = []
    slept: list[float] = []
    ctl = SetupController(
        net, DEVICE, on_joined=lambda r: urls.append(r.hub_url or ""), sleep=slept.append
    )
    return ctl, urls, slept


def test_first_start_without_network_opens_setup() -> None:
    net = SimNetwork()
    ctl, _, _ = make(net)
    ctl.tick(0.0)
    assert ctl.active
    assert net.hotspot == "allskyhub-ABCD" == setup_ssid(DEVICE)


def test_ethernet_never_opens_setup() -> None:
    net = SimNetwork(ethernet=True)
    ctl, _, _ = make(net)
    for t in range(0, 1000, 10):
        ctl.tick(float(t))
    assert not ctl.active


def test_lost_wifi_opens_setup_after_two_minutes() -> None:
    net = SimNetwork(passwords={"Home": "secretpw"})
    net.join("Home", "secretpw", "AT")
    ctl, _, _ = make(net)
    ctl.tick(0.0)
    net.connected = False
    ctl.tick(10.0)
    ctl.tick(10.0 + UNREACHABLE_S - 1)
    assert not ctl.active
    ctl.tick(10.0 + UNREACHABLE_S)
    assert ctl.active


def test_idle_setup_mode_closes() -> None:
    net = SimNetwork()
    ctl, _, _ = make(net)
    ctl.tick(0.0)
    ctl.touch(100.0)
    ctl.tick(100.0 + IDLE_S - 1)
    assert ctl.active
    ctl.tick(100.0 + IDLE_S)
    assert not ctl.active
    assert net.hotspot is None


def test_join_success_switches_hub() -> None:
    net = SimNetwork(passwords={"Home": "secretpw"})
    ctl, urls, _ = make(net)
    ctl.tick(0.0)
    ctl.request_network(NetworkRequest("Home", "secretpw", "AT", "https://hub.example"), 1.0)
    ctl.tick(2.0)
    assert not ctl.active
    assert net.connected
    assert ctl.last_error is None
    assert urls == ["https://hub.example"]


@pytest.mark.parametrize(
    ("ssid", "pw", "internet", "error"),
    [
        ("Home", "wrongpass", True, JoinError.WIFI_AUTH),
        ("Nope", "secretpw", True, JoinError.WIFI_NOT_FOUND),
        ("Home", "secretpw", False, JoinError.NO_INTERNET),
    ],
)
def test_join_failure_reopens_setup(ssid: str, pw: str, internet: bool, error: JoinError) -> None:
    net = SimNetwork(passwords={"Home": "secretpw"})
    net.internet = internet
    ctl, urls, slept = make(net)
    ctl.tick(0.0)
    ctl.request_network(NetworkRequest(ssid, pw, "AT", None), 1.0)
    ctl.tick(2.0)
    assert ctl.active
    assert net.hotspot == "allskyhub-ABCD"
    assert ctl.last_error is error
    assert urls == []
    if error is JoinError.NO_INTERNET:
        assert sum(slept) >= 30


def test_setup_file_parse_and_apply(tmp_path: Path) -> None:
    f = tmp_path / "allskyhub-setup.json"
    f.write_text(
        json.dumps(
            {
                "allskyhub_setup": 1,
                "wifi": {"ssid": "Home", "password": "secretpw"},
                "wifi_country": "at",
                "hub_url": "https://hub.example",
            }
        )
    )
    net = SimNetwork(passwords={"Home": "secretpw"})
    applied = apply_once(f, net)
    assert applied is not None
    assert applied.country == "AT"
    assert applied.hub_url == "https://hub.example"
    assert net.configured == "Home"
    assert not f.exists()


@pytest.mark.parametrize(
    "data",
    [
        b"not json",
        b'{"allskyhub_setup": 2}',
        b'{"allskyhub_setup": 1, "wifi": {"ssid": ""}}',
        b'{"allskyhub_setup": 1, "wifi": {"ssid": "x", "password": "short"}}',
        b'{"allskyhub_setup": 1, "wifi_country": "AUT"}',
        b'{"allskyhub_setup": 1, "hub_url": "ftp://x"}',
    ],
)
def test_setup_file_rejects_bad_input(data: bytes, tmp_path: Path) -> None:
    with pytest.raises(SetupFileError):
        parse(data)
    f = tmp_path / "allskyhub-setup.json"
    f.write_bytes(data)
    assert apply_once(f, SimNetwork()) is None
    assert not f.exists()
    assert (tmp_path / "allskyhub-setup.failed.json").exists()


def test_settings_roundtrip_and_defaults(tmp_path: Path) -> None:
    p = tmp_path / "s" / "settings.json"
    assert AgentSettings.load(p).hub_url == DEFAULT_HUB
    AgentSettings(hub_url="https://hub.example").save(p)
    assert AgentSettings.load(p).hub_url == "https://hub.example"
    p.write_text("[]")
    assert AgentSettings.load(p).hub_url == DEFAULT_HUB


def test_discovery_record() -> None:
    info = service_info(DEVICE, 8080, ["192.168.1.20"])
    assert info.type == SERVICE_TYPE == "_allskyhub._tcp.local."
    assert info.server == "allskyhub-abcd.local."
    assert host_name(DEVICE) == "allskyhub-abcd"
    assert info.port == 8080
    assert info.properties == {b"id": DEVICE.encode(), b"v": b"1"}


def _call(url: str, body: bytes | None = None) -> tuple[int, dict[str, object] | list[object]]:
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw.startswith(b"{") else {}


def test_setup_endpoints(tmp_path: Path) -> None:
    net = SimNetwork(
        networks=[WifiNetwork("Home", 60, True), WifiNetwork("Cafe", 80, False)],
        passwords={"Home": "secretpw"},
    )
    ctl, _, _ = make(net)
    pairing = PairingState(DEVICE, "https://allskyhub.org", "sim", "0.1.0")
    # Treat loopback as the setup network for this test.
    web = WebServer(LiveState(), "127.0.0.1", 0, pairing, ctl, setup_net="127.0.0.0/8")
    web.start()
    base = f"http://127.0.0.1:{web.port}"
    try:
        # Not in setup mode yet: setup endpoints are closed.
        assert _call(base + "/api/wifi/networks")[0] == 403
        status, info = _call(base + "/api/setup")
        assert status == 200
        assert isinstance(info, dict)
        assert info["setup_mode"] is False
        assert info["last_error"] is None

        ctl.tick(0.0)
        status, nets = _call(base + "/api/wifi/networks")
        assert status == 200
        assert nets == [
            {"ssid": "Cafe", "signal": 80, "secure": False},
            {"ssid": "Home", "signal": 60, "secure": True},
        ]

        status, err = _call(base + "/api/setup/network", b'{"ssid": "Home", "password": "x"}')
        assert status == 400
        assert isinstance(err, dict)
        assert err["fields"] == ["country", "password"]
        assert b"x" not in json.dumps(err).encode().replace(b"fields", b"")

        body = b'{"ssid": "Home", "password": "secretpw", "country": "at"}'
        status, ok = _call(base + "/api/setup/network", body)
        assert (status, ok) == (202, {"will_join": "Home"})
        ctl.tick(1.0)
        assert net.connected
        status, info = _call(base + "/api/setup")
        assert isinstance(info, dict)
        assert info["setup_mode"] is False
    finally:
        web.stop()


def test_setup_endpoints_refuse_other_networks(tmp_path: Path) -> None:
    net = SimNetwork()
    ctl, _, _ = make(net)
    ctl.tick(0.0)
    pairing = PairingState(DEVICE, "https://allskyhub.org", "sim", "0.1.0")
    web = WebServer(LiveState(), "127.0.0.1", 0, pairing, ctl)  # default 10.42.0.0/24
    web.start()
    try:
        base = f"http://127.0.0.1:{web.port}"
        assert _call(base + "/api/wifi/networks")[0] == 403
        body = b'{"ssid": "Home", "password": "secretpw", "country": "AT"}'
        assert _call(base + "/api/setup/network", body)[0] == 403
    finally:
        web.stop()


def test_settings_location_timezone_camera(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    s = AgentSettings().with_updates(latitude=48.1, longitude=14.4, timezone="Europe/Vienna")
    assert s.has_location
    s.save(p)
    back = AgentSettings.load(p)
    assert (back.latitude, back.longitude, back.timezone, back.camera) == (
        48.1,
        14.4,
        "Europe/Vienna",
        "auto",
    )
    # Bad values are ignored, the rest is kept.
    assert back.with_updates(timezone="Mars/Olympus", camera="nikon") == back
    assert back.with_updates(latitude=1.0) == back  # only both coordinates together
    p.write_text('{"latitude": 91, "longitude": 10, "timezone": "Europe/Vienna"}')
    assert not AgentSettings.load(p).has_location


def test_setup_file_location_timezone_camera(tmp_path: Path) -> None:
    f = parse(
        b'{"allskyhub_setup": 1, "location": {"latitude": 48.14, "longitude": 14.39},'
        b' "timezone": "Europe/Vienna", "camera": "sim"}'
    )
    assert (f.latitude, f.longitude, f.timezone, f.camera) == (48.14, 14.39, "Europe/Vienna", "sim")
    for bad in (
        b'{"allskyhub_setup": 1, "location": {"latitude": 99, "longitude": 0}}',
        b'{"allskyhub_setup": 1, "timezone": "Nowhere/Town"}',
        b'{"allskyhub_setup": 1, "camera": "canon"}',
    ):
        with pytest.raises(SetupFileError):
            parse(bad)


def test_setup_network_with_location(tmp_path: Path) -> None:
    net = SimNetwork(passwords={"Home": "secretpw"})
    joined: list[NetworkRequest] = []
    ctl = SetupController(net, DEVICE, on_joined=joined.append, sleep=lambda s: None)
    settings_file = tmp_path / "settings.json"
    pairing = PairingState(DEVICE, "https://allskyhub.org", "sim", "0.1.0")
    web = WebServer(
        LiveState(),
        "127.0.0.1",
        0,
        pairing,
        ctl,
        setup_net="127.0.0.0/8",
        settings=lambda: AgentSettings.load(settings_file),
    )
    web.start()
    base = f"http://127.0.0.1:{web.port}"
    try:
        ctl.tick(0.0)
        _, info = _call(base + "/api/setup")
        assert isinstance(info, dict)
        assert info["location_set"] is False
        assert info["timezone"] == "UTC"

        body = b'{"ssid": "Home", "password": "secretpw", "country": "AT", "latitude": 48.1}'
        status, err = _call(base + "/api/setup/network", body)
        assert status == 400
        assert isinstance(err, dict)
        assert err["fields"] == ["latitude", "longitude"]

        body = (
            b'{"ssid": "Home", "password": "secretpw", "country": "AT",'
            b' "latitude": 48.1, "longitude": 14.4, "timezone": "Not/AZone"}'
        )
        status, err = _call(base + "/api/setup/network", body)
        assert status == 400
        assert isinstance(err, dict)
        assert err["fields"] == ["timezone"]

        body = (
            b'{"ssid": "Home", "password": "secretpw", "country": "AT",'
            b' "latitude": 48.1, "longitude": 14.4, "timezone": "Europe/Vienna"}'
        )
        assert _call(base + "/api/setup/network", body)[0] == 202
        ctl.tick(1.0)
        assert len(joined) == 1
        req = joined[0]
        assert (req.latitude, req.longitude, req.timezone) == (48.1, 14.4, "Europe/Vienna")
        AgentSettings().with_updates(
            latitude=req.latitude, longitude=req.longitude, timezone=req.timezone
        ).save(settings_file)
        _, info = _call(base + "/api/setup")
        assert isinstance(info, dict)
        assert info["location_set"] is True
        assert info["timezone"] == "Europe/Vienna"
    finally:
        web.stop()


def test_settings_delays(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    s = AgentSettings().with_updates(day_delay_s=60, night_delay_s=5)
    s.save(p)
    back = AgentSettings.load(p)
    assert (back.day_delay_s, back.night_delay_s) == (60.0, 5.0)
    assert back.with_updates(night_delay_s=-3) == back
