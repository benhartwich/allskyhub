"""System facts for the `status` message (SPEC §6.3). Every reader fails soft to None/False."""

from __future__ import annotations

import shutil
from pathlib import Path

# systemd-timesyncd creates this file once the clock is synchronized (Raspberry Pi OS).
_TIMESYNC_FLAG = Path("/run/systemd/timesync/synchronized")
_CPU_TEMP = Path("/sys/class/thermal/thermal_zone0/temp")
_UPTIME = Path("/proc/uptime")


def time_trusted(flag: Path = _TIMESYNC_FLAG) -> bool:
    """SPEC §4.4: True once the system clock has been NTP-synchronized."""
    return flag.exists()


def cpu_temp_c(path: Path = _CPU_TEMP) -> float | None:
    try:
        return round(int(path.read_text().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def disk_free_pct(path: Path) -> float | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return round(100.0 * usage.free / usage.total, 1) if usage.total else None


def uptime_s(path: Path = _UPTIME) -> int:
    try:
        return int(float(path.read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0
