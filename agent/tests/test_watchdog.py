"""Watchdog: heartbeat and the escalation ladder (SPEC §8.1, roadmap L17)."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from allskyhub_agent.live import LiveState
from allskyhub_agent.watchdog import (
    Heartbeat,
    WatchState,
    decide,
    sd_notify,
    usb_camera_devices,
    usb_reset,
)

T0 = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)


def run(st: WatchState, minutes: list[int], frames: int | None = 5, net_ok: bool = True,
        busy: bool = False) -> dict[int, str]:  # fmt: skip
    """Run the check at these minutes after T0; the actions taken."""
    out: dict[int, str] = {}
    for m in minutes:
        action = decide(st, T0 + timedelta(minutes=m), frames, net_ok, busy)
        if action:
            out[m] = action
    return out


def test_ladder_when_frames_stop() -> None:
    st = WatchState()
    assert run(st, list(range(0, 125, 5))) == {
        20: "restart", 35: "usb_reset", 60: "reboot",
    }  # fmt: skip
    # After the reboot nothing more for 6 hours, then the ladder ends in a reboot again.
    assert run(st, list(range(125, 300, 5))) == {}


def test_frames_coming_again_reset_the_ladder() -> None:
    st = WatchState()
    assert run(st, [0, 20]) == {20: "restart"}
    assert run(st, [25], frames=6) == {}  # the restart helped
    assert run(st, [30, 40, 45], frames=6) == {45: "restart"}  # a new stall: again a restart


def test_agent_not_answering_counts_as_stalled() -> None:
    st = WatchState()
    assert run(st, [0], frames=3) == {}
    assert run(st, [10, 20], frames=None) == {20: "restart"}


def capturing(st: WatchState, minutes: list[int], net_ok: bool) -> dict[int, str]:
    """Frames keep coming; only the network varies."""
    out: dict[int, str] = {}
    for m in minutes:
        if action := decide(st, T0 + timedelta(minutes=m), m, net_ok, False):
            out[m] = action
    return out


def test_network() -> None:
    st = WatchState()
    assert capturing(st, [0, 60, 120, 180, 240], net_ok=False) == {}  # never had a network
    st = WatchState()
    capturing(st, [0], net_ok=True)
    assert capturing(st, [5, 60, 125, 184, 185, 200], net_ok=False) == {185: "reboot"}
    # Down again: 3 hours would be reached at 490, but the last reboot is not 6 hours ago.
    assert capturing(st, [300, 450, 490], net_ok=False) == {}
    assert capturing(st, [560], net_ok=False) == {560: "reboot"}


def test_nothing_while_the_updater_switches() -> None:
    st = WatchState()
    assert run(st, [0, 30, 60], busy=True) == {}


def test_state_survives(tmp_path: Path) -> None:
    st = WatchState()
    run(st, [0, 20])
    st.save(tmp_path / "s.json")
    again = WatchState.load(tmp_path / "s.json")
    assert again.last_restart == st.last_restart
    assert WatchState.load(tmp_path / "missing.json") == WatchState()


def test_heartbeat_pings_only_while_frames_come() -> None:
    clock = [0.0]
    frames = [0]
    sent: list[str] = []
    hb = Heartbeat(lambda: frames[0], max_age_s=600, grace_s=1200,
                   notify=lambda m: sent.append(m) is None, monotonic=lambda: clock[0])  # fmt: skip
    assert hb.beat()  # looking for the camera: within the grace time
    clock[0] = 1300
    assert not hb.beat()  # no frame at all after the grace time
    frames[0] = 1
    assert hb.beat()
    clock[0] = 1300 + 599
    assert hb.beat()
    clock[0] = 1300 + 601
    assert not hb.beat()  # capture hangs: systemd restarts the agent
    assert sent == ["WATCHDOG=1"] * 3


def test_sd_notify(tmp_path: Path) -> None:
    path = tmp_path / "notify"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as srv:
        srv.bind(str(path))
        assert sd_notify("WATCHDOG=1", {"NOTIFY_SOCKET": str(path)})
        assert srv.recv(64) == b"WATCHDOG=1"
    assert not sd_notify("WATCHDOG=1", {})


def test_usb_reset(tmp_path: Path) -> None:
    for name, vendor in (("1-1.2", "03c3"), ("1-1.3", "046d")):
        d = tmp_path / name
        d.mkdir()
        (d / "idVendor").write_text(vendor + "\n")
        (d / "authorized").write_text("1")
    found = usb_camera_devices(tmp_path)
    assert [d.name for d in found] == ["1-1.2"]
    assert usb_reset(found, sleep=lambda _: None) == 1
    assert (tmp_path / "1-1.2" / "authorized").read_text() == "1"


def test_health_is_open() -> None:
    live = LiveState()
    assert live.health() == {"version": live.status()["version"], "frames": 0, "frame": None}
