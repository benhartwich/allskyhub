"""Reboot by day for system security updates (SPEC §8 System updates).

unattended-upgrades installs Debian and Raspberry Pi OS security updates but never reboots;
a new kernel or libc only takes effect after one. When `/run/reboot-required` exists the
agent reboots once it is day: the sun at least 10° up, local time 10:00 to 16:00, no
product being built, no agent update in progress, at most once a day. Until then
`status.reboot_required` is true.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, time, tzinfo
from pathlib import Path

log = logging.getLogger(__name__)

REQUIRED = Path("/run/reboot-required")
STATE_NAME = "security-reboot"  # in <data>: the local date of the last such reboot
CHECK_S = 600.0
MIN_SUN_DEG = 10.0
WINDOW = (time(10, 0), time(16, 0))


def due(
    local: datetime, sun_deg: float | None, busy: bool, updating: bool, last: date | None
) -> bool:
    """Whether a required reboot may happen now."""
    return (
        sun_deg is not None
        and sun_deg >= MIN_SUN_DEG
        and WINDOW[0] <= local.time() < WINDOW[1]
        and not busy
        and not updating
        and last != local.date()
    )


class SecurityReboot:
    def __init__(
        self,
        state: Path,
        tz: Callable[[], tzinfo],
        sun_deg: Callable[[datetime], float | None],
        busy: Callable[[], bool],
        updating: Callable[[], bool],
        reboot: Callable[[], str | None],
        flag: Path = REQUIRED,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._state = state
        self._tz = tz
        self._sun = sun_deg
        self._busy = busy
        self._updating = updating
        self._reboot = reboot
        self._flag = flag
        self._now = now
        self._stop = threading.Event()

    @property
    def required(self) -> bool:
        return self._flag.exists()

    def _last(self) -> date | None:
        try:
            return date.fromisoformat(self._state.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def check(self) -> bool:
        """Reboot if it is required and due; True if a reboot was asked for."""
        if not self.required:
            return False
        now = self._now()
        local = now.astimezone(self._tz())
        if not due(local, self._sun(now), self._busy(), self._updating(), self._last()):
            return False
        try:
            self._state.write_text(local.date().isoformat() + "\n", encoding="utf-8")
        except OSError:
            log.exception("cannot note the security reboot; not rebooting")
            return False
        log.warning("rebooting for system security updates (%s)", self._flag)
        why = self._reboot()
        if why is not None:
            log.warning("security reboot refused: %s", why)
        return True

    def run(self) -> None:
        while not self._stop.wait(CHECK_S):
            try:
                self.check()
            except Exception:  # never stop the agent over this
                log.exception("security reboot check failed")

    def start(self) -> None:
        threading.Thread(target=self.run, name="security-reboot", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
