"""System facts for the `status` message (SPEC §6.3). Every reader fails soft to None/False."""

from __future__ import annotations

import shutil
import subprocess
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


# --- the local UI's system page (roadmap L12) -------------------------------------------
_LOADAVG = Path("/proc/loadavg")
_MEMINFO = Path("/proc/meminfo")
_MODEL = Path("/proc/device-tree/model")
_OS_RELEASE = Path("/etc/os-release")
_HWMON = Path("/sys/class/hwmon")
# vcgencmd get_throttled bits: now (0-3) and since boot (16-19).
_THROTTLE_BITS = {
    0: "under_voltage",
    1: "freq_capped",
    2: "throttled",
    3: "temp_limit",
    16: "under_voltage_seen",
    17: "freq_capped_seen",
    18: "throttled_seen",
    19: "temp_limit_seen",
}


def load_avg(path: Path = _LOADAVG) -> list[float] | None:
    try:
        return [float(x) for x in path.read_text().split()[:3]]
    except (OSError, ValueError):
        return None


def memory(path: Path = _MEMINFO) -> dict[str, int] | None:
    """Total and available memory in bytes."""
    try:
        rows = dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)
        total = int(rows["MemTotal"].split()[0]) * 1024
        available = int(rows["MemAvailable"].split()[0]) * 1024
    except (OSError, ValueError, KeyError, IndexError):
        return None
    return {"total": total, "available": available}


def disk(path: Path) -> dict[str, int] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total": usage.total, "free": usage.free}


def model(path: Path = _MODEL) -> str | None:
    try:
        return path.read_text().strip("\x00\n ") or None
    except OSError:
        return None


def os_name(path: Path = _OS_RELEASE) -> str | None:
    try:
        for line in path.read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        pass
    return None


def power_flags(vcgencmd: str | None = None, hwmon: Path = _HWMON) -> list[str] | None:
    """Raspberry Pi power and heat warnings now and since boot (`vcgencmd
    get_throttled`), else under-voltage now from the rpi_volt sensor; None off a Pi."""
    exe = vcgencmd or shutil.which("vcgencmd")
    if exe is not None:
        try:
            out = subprocess.run(  # noqa: S603 - fixed arguments
                [exe, "get_throttled"], capture_output=True, text=True, timeout=5, check=False
            ).stdout
            value = int(out.strip().split("=", 1)[1], 16)
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
        else:
            return [name for bit, name in _THROTTLE_BITS.items() if value & (1 << bit)]
    try:
        for d in hwmon.iterdir():
            if (d / "name").read_text().strip() == "rpi_volt":
                alarm = (d / "in0_lcrit_alarm").read_text().strip()
                return ["under_voltage"] if alarm == "1" else []
    except OSError:
        pass
    return None
