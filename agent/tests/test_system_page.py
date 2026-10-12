"""The local UI's system page (roadmap L12)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from allskyhub_agent import system
from allskyhub_agent.live import LiveState
from allskyhub_agent.web.server import SystemPage, WebServer


def test_memory_load_model(tmp_path: Path) -> None:
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal:  8000000 kB\nMemFree:  100 kB\nMemAvailable:  6000000 kB\n")
    assert system.memory(mem) == {"total": 8_192_000_000, "available": 6_144_000_000}
    assert system.memory(tmp_path / "missing") is None
    load = tmp_path / "loadavg"
    load.write_text("0.97 1.06 1.01 2/312 591167\n")
    assert system.load_avg(load) == [0.97, 1.06, 1.01]
    model = tmp_path / "model"
    model.write_bytes(b"Raspberry Pi 4 Model B Rev 1.5\x00")
    assert system.model(model) == "Raspberry Pi 4 Model B Rev 1.5"
    osr = tmp_path / "os-release"
    osr.write_text('NAME="Debian"\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n')
    assert system.os_name(osr) == "Debian GNU/Linux 13 (trixie)"


def test_power_flags(tmp_path: Path) -> None:
    fake = tmp_path / "vcgencmd"
    fake.write_text("#!/bin/sh\necho throttled=0x50005\n")
    fake.chmod(0o755)
    assert system.power_flags(str(fake)) == [
        "under_voltage",
        "throttled",
        "under_voltage_seen",
        "throttled_seen",
    ]
    # Without vcgencmd: under-voltage now from the rpi_volt sensor.
    sensor = tmp_path / "hwmon" / "hwmon1"
    sensor.mkdir(parents=True)
    (sensor / "name").write_text("rpi_volt\n")
    (sensor / "in0_lcrit_alarm").write_text("1\n")
    missing = str(tmp_path / "no-vcgencmd")
    assert system.power_flags(missing, tmp_path / "hwmon") == ["under_voltage"]
    assert system.power_flags(missing, tmp_path / "nothing") is None


@pytest.fixture
def web() -> Iterator[tuple[int, list[str]]]:
    asked: list[str] = []

    def action(name: str) -> str | None:
        asked.append(name)
        return "Nicht erlaubt." if name == "shutdown" else None

    page = SystemPage(lambda: {"uptime_s": 42}, action)
    server = WebServer(LiveState(), "127.0.0.1", 0, system_page=page)
    server.start()
    yield server.port, asked
    server.stop()


def _call(port: int, path: str, post: bool = False) -> tuple[int, Any]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"" if post else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_routes(web: tuple[int, list[str]]) -> None:
    port, asked = web
    assert _call(port, "/api/system") == (200, {"uptime_s": 42})
    assert _call(port, "/api/system/reboot", post=True) == (202, {"action": "reboot"})
    status, body = _call(port, "/api/system/shutdown", post=True)
    assert status == 503
    assert json.loads(body) == {"detail": "Nicht erlaubt."}
    assert _call(port, "/api/system/format", post=True)[0] == 404
    assert asked == ["reboot", "shutdown"]
