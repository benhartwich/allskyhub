"""Keep the camera working without anyone at it (SPEC §8.1, roadmap L17).

Two levels:

- **Heartbeat** in the agent: tells systemd "still fine" (`sd_notify WATCHDOG=1`) only
  while frames keep coming. If capture hangs (a stuck USB call), the pings stop and
  systemd restarts the agent after `WatchdogSec`.
- **Watchdog** run as root every few minutes by `allskyhub-watchdog.timer`: if no frame
  came for a long time it escalates (restart the agent, then reset the USB camera, then
  reboot), and it reboots after hours without the local network (gateway and DNS). It
  never acts because the hub is down: capture does not depend on the hub (rule 1).

Decisions are pure (`decide`), so they are tested without a camera or root.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# --- heartbeat (agent side) ------------------------------------------------------------------


def sd_notify(message: str, env: dict[str, str] | None = None) -> bool:
    """Send a message to systemd's notify socket; False when not run by systemd."""
    path = (env if env is not None else os.environ).get("NOTIFY_SOCKET")
    if not path:
        return False
    addr = "\0" + path[1:] if path.startswith("@") else path
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode())
    except OSError:
        return False
    return True


class Heartbeat:
    """Pings systemd while the frame count keeps rising (monotonic clock, so a wrong
    system time does not matter); allows `grace_s` after start to find the camera."""

    def __init__(
        self,
        frames: Callable[[], int],
        max_age_s: float = 600.0,
        paused: Callable[[], bool] = lambda: False,
        grace_s: float = 1200.0,
        interval_s: float = 30.0,
        notify: Callable[[str], bool] = sd_notify,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._frames = frames
        self._paused = paused
        self._max_age = max_age_s
        self._interval = interval_s
        self._notify = notify
        self._monotonic = monotonic
        start = monotonic()
        self._deadline_first = start + grace_s
        self._count = frames()
        self._last_new: float | None = None
        self._stop = threading.Event()

    def beat(self) -> bool:
        """One check; True if it pinged."""
        now = self._monotonic()
        count = self._frames()
        if count != self._count or self._paused():
            # A pause (SPEC §4.1) is no stall: it counts as a fresh frame.
            self._count, self._last_new = count, now
        fresh = (
            now - self._last_new < self._max_age
            if self._last_new is not None
            else now < self._deadline_first
        )
        return fresh and self._notify("WATCHDOG=1")

    def start(self) -> None:
        if not os.environ.get("WATCHDOG_USEC"):
            return  # not under a systemd watchdog

        def run() -> None:
            while not self._stop.wait(self._interval):
                self.beat()

        threading.Thread(target=run, name="heartbeat", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


# --- watchdog (root side) ---------------------------------------------------------------------

STALL_RESTART = timedelta(minutes=20)
STALL_USB = timedelta(minutes=35)
STALL_REBOOT = timedelta(minutes=60)
STEP_GAP = timedelta(minutes=15)  # wait this long after one step before the next
NET_REBOOT = timedelta(hours=3)
REBOOT_GAP = timedelta(hours=6)


@dataclass
class WatchState:
    frames: int | None = None
    frames_changed: str | None = None  # ISO time the count last rose
    net_down_since: str | None = None
    net_seen: bool = False  # a camera that never had a network does not reboot for it
    last_restart: str | None = None
    last_usb_reset: str | None = None
    last_reboot: str | None = None
    extra: dict[str, Any] = field(default_factory=dict[str, Any])

    @classmethod
    def load(cls, path: Path) -> WatchState:
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__ and k != "extra"}  # pyright: ignore[reportUnknownVariableType]
        try:
            return cls(**known)  # pyright: ignore[reportUnknownArgumentType]
        except TypeError:
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self)), encoding="utf-8")
        tmp.replace(path)


def _t(raw: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _since(now: datetime, raw: str | None, gap: timedelta) -> bool:
    """True if `raw` is unset or at least `gap` before `now`."""
    at = _t(raw)
    return at is None or now - at >= gap


def decide(
    st: WatchState,
    now: datetime,
    frames: int | None,
    net_ok: bool,
    busy: bool,
    paused: bool = False,
) -> str | None:
    """The next step: "restart", "usb_reset", "reboot" or None; updates `st`.

    `frames` is the agent's frame count (None: the agent does not answer), `net_ok`
    whether the gateway or DNS answers, `busy` whether the updater is switching versions,
    `paused` whether the owner paused capture (SPEC §4.1): no stall then.
    """
    iso = now.isoformat()
    if paused:
        st.frames_changed = iso
    if frames is not None and frames != st.frames:
        st.frames, st.frames_changed = frames, iso
    if st.frames_changed is None:
        st.frames_changed = iso  # first run: count from now
    st.net_seen = st.net_seen or net_ok
    st.net_down_since = None if net_ok or not st.net_seen else (st.net_down_since or iso)
    if busy:
        return None
    net_down = _t(st.net_down_since)
    if (
        net_down is not None
        and now - net_down >= NET_REBOOT
        and _since(now, st.last_reboot, REBOOT_GAP)
    ):
        st.last_reboot, st.net_down_since = iso, None
        return "reboot"
    changed = _t(st.frames_changed) or now
    stall = now - changed
    if stall >= STALL_REBOOT and st.last_usb_reset and _since(now, st.last_usb_reset, STEP_GAP):
        if _since(now, st.last_reboot, REBOOT_GAP):
            st.last_reboot = iso
            return "reboot"
        return None
    if (
        stall >= STALL_USB
        and st.last_restart
        and _since(now, st.last_restart, STEP_GAP)
        and _since(now, st.last_usb_reset, STALL_REBOOT)
    ):
        st.last_usb_reset = iso
        return "usb_reset"
    if (
        stall >= STALL_RESTART
        and _since(now, st.last_restart, STEP_GAP)
        and (st.last_usb_reset is None or _since(now, st.last_restart, STALL_REBOOT))
    ):
        st.last_restart = iso
        return "restart"
    if stall < STALL_RESTART:
        st.last_restart = st.last_usb_reset = None  # healthy again: start the ladder anew
    return None


def usb_camera_devices(sys_usb: Path = Path("/sys/bus/usb/devices")) -> list[Path]:
    """USB devices of ZWO cameras (vendor 03c3)."""
    out: list[Path] = []
    try:
        for d in sys_usb.iterdir():
            try:
                if (d / "idVendor").read_text().strip() == "03c3":
                    out.append(d)
            except OSError:
                continue
    except OSError:
        pass
    return out


def usb_reset(devices: list[Path], sleep: Callable[[float], None] = time.sleep) -> int:
    """Unplug and plug in again in software (`authorized` 0, then 1); how many."""
    done = 0
    for d in devices:
        try:
            (d / "authorized").write_text("0")
            sleep(2.0)
            (d / "authorized").write_text("1")
            done += 1
        except OSError as exc:
            log.warning("USB reset of %s failed: %s", d.name, exc)
    return done
