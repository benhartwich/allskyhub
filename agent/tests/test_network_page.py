"""The local UI's network section (roadmap L13)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from allskyhub_agent.adapters import netinfo
from allskyhub_agent.adapters.network import SimNetwork
from allskyhub_agent.live import LiveState
from allskyhub_agent.setup.controller import SetupController
from allskyhub_agent.web.server import WebServer

ADDR = json.dumps([
    {"ifname": "lo", "link_type": "loopback", "operstate": "UNKNOWN",
     "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
    {"ifname": "eth0", "link_type": "ether", "operstate": "DOWN", "addr_info": []},
    {"ifname": "wlan0", "link_type": "ether", "operstate": "UP", "addr_info": [
        {"family": "inet", "local": "192.168.254.53", "prefixlen": 24},
        {"family": "inet6", "local": "fe80::1", "prefixlen": 64},
        {"family": "inet6", "local": "2a02:8388::5", "prefixlen": 64}]},
    {"ifname": "docker0", "link_type": "ether", "operstate": "UP", "addr_info": []},
])  # fmt: skip
ROUTE = json.dumps([{"dst": "default", "gateway": "192.168.254.1", "dev": "wlan0"}])


def test_interfaces(tmp_path: Path) -> None:
    for name in ("eth0", "wlan0"):
        (tmp_path / name / "device").mkdir(parents=True)
    (tmp_path / "wlan0" / "wireless").mkdir()
    (tmp_path / "docker0").mkdir()  # virtual: no device behind it
    got = netinfo.interfaces(ADDR, ROUTE, tmp_path)
    assert got == [
        {"name": "eth0", "kind": "ethernet", "up": False, "addresses": [], "default": False},
        {"name": "wlan0", "kind": "wifi", "up": True,
         "addresses": ["192.168.254.53/24", "2a02:8388::5/64"], "default": True},
    ]  # fmt: skip
    assert netinfo.interfaces("not json", "", tmp_path) == []


def test_wifi() -> None:
    assert netinfo.wifi("no:Nachbar:80\nyes:B&B Outdoor:54\n") == {
        "ssid": "B&B Outdoor",
        "signal": 54,
    }
    assert netinfo.wifi("yes:Mein\\:Netz:70\n") == {"ssid": "Mein:Netz", "signal": 70}
    assert netinfo.wifi("no:Nachbar:80\n") is None


@pytest.fixture
def web() -> Iterator[tuple[int, SetupController]]:
    net = SimNetwork(passwords={"Home": "secretpw"})
    setup = SetupController(net, "k44i7rl6cuaek5yufvr7oh3c5i")
    info = {"hostname": "allsky", "interfaces": [], "wifi": None}
    server = WebServer(LiveState(), "127.0.0.1", 0, setup=setup, network_info=lambda: info)
    server.start()
    yield server.port, setup
    server.stop()


def _call(port: int, path: str, body: object = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_routes(web: tuple[int, SetupController]) -> None:
    port, setup = web
    status, info = _call(port, "/api/network")
    assert status == 200
    assert info["managed"]
    status, nets = _call(port, "/api/network/wifi")
    assert status == 200
    assert isinstance(nets, list)
    # Never another hub from here, and the same password rules as setup mode.
    status, err = _call(port, "/api/network/wifi", {"ssid": "Home", "country": "AT",
                                                    "hub_url": "https://evil.example"})  # fmt: skip
    assert status == 400
    assert err["fields"] == ["hub_url"]
    status, err = _call(
        port, "/api/network/wifi", {"ssid": "Home", "password": "short", "country": "AT"}
    )
    assert (status, err["fields"]) == (400, ["password"])
    status, ok = _call(
        port, "/api/network/wifi", {"ssid": "Home", "password": "secretpw", "country": "at"}
    )
    assert (status, ok) == (202, {"will_join": "Home"})
    assert setup._pending is not None  # pyright: ignore[reportPrivateUsage]
    assert (setup._pending.country, setup._pending.hub_url) == ("AT", None)  # pyright: ignore[reportPrivateUsage]


def test_not_managed() -> None:
    server = WebServer(LiveState(), "127.0.0.1", 0, network_info=lambda: {"interfaces": []})
    server.start()
    try:
        assert _call(server.port, "/api/network")[1]["managed"] is False
        assert _call(server.port, "/api/network/wifi")[0] == 404
        assert _call(server.port, "/api/network/wifi", {"ssid": "x", "country": "AT"})[0] == 404
    finally:
        server.stop()
