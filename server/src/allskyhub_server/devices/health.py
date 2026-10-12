"""Is the camera all right? (roadmap C14)

Judged from what the camera last reported (SPEC §6.3 status, frame info) and whether it
is connected. The camera page shows the result; the notifier reports each problem once
when it appears (and once when it is gone).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal, cast

from allskyhub_server.models import Device

OFFLINE_AFTER = dt.timedelta(minutes=30)
# No new picture for this long while connected: capture is stuck or the camera is gone.
# At least three times the configured pause between frames.
STALE_FRAMES = dt.timedelta(minutes=20)
DISK_WARN, DISK_ERROR = 20.0, 10.0
SENSOR_HOT, CPU_HOT = 55.0, 80.0


@dataclass(frozen=True)
class Problem:
    key: str
    level: Literal["warn", "error"]
    text: str


def _age(delta: dt.timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 120:
        return f"{minutes} Minuten"
    hours = minutes // 60
    return f"{hours} Stunden" if hours < 48 else f"{hours // 24} Tagen"


def _when(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def check(device: Device, online: bool, now: dt.datetime) -> list[Problem]:
    problems: list[Problem] = []
    if not online:
        seen = device.last_seen_at
        if seen is None or now - seen >= OFFLINE_AFTER:
            since = f" seit {_age(now - seen)}" if seen else ""
            problems.append(Problem("offline", "error", f"Die Kamera ist offline{since}."))
        return problems  # the rest is about a running camera

    status: dict[str, Any] = device.last_status or {}
    settings = cast(dict[str, Any], status.get("settings") or {})
    delays = [v for v in (settings.get("day_delay_s"), settings.get("night_delay_s"))
              if isinstance(v, int | float)]  # fmt: skip
    stale = max(STALE_FRAMES, dt.timedelta(seconds=3 * max(delays, default=0)))
    captured = _when((device.last_frame or {}).get("captured_at"))
    if captured is None or now - captured > stale:
        since = f" seit {_age(now - captured)}" if captured else ""
        problems.append(
            Problem("no_frames", "error", f"Verbunden, aber keine neuen Bilder{since}.")
        )

    disk = status.get("disk_free_pct")
    if isinstance(disk, int | float) and disk < DISK_WARN:
        level = "error" if disk < DISK_ERROR else "warn"
        problems.append(Problem("disk", level, f"Nur noch {round(disk)} % Speicher frei."))
    sensor = status.get("sensor_temp_c")
    if isinstance(sensor, int | float) and sensor >= SENSOR_HOT:
        problems.append(Problem("sensor_hot", "warn", f"Sensor sehr warm: {round(sensor)} °C."))
    cpu = status.get("cpu_temp_c")
    if isinstance(cpu, int | float) and cpu >= CPU_HOT:
        problems.append(Problem("cpu_hot", "warn", f"Prozessor sehr heiß: {round(cpu)} °C."))
    if status.get("time_trusted") is False:
        problems.append(Problem("clock", "warn", "Die Uhr der Kamera ist nicht synchronisiert."))
    update = cast(dict[str, Any], status.get("update") or {})
    if update.get("state") in ("failed", "rolled_back"):
        problems.append(Problem("update", "warn", "Das letzte Software-Update ist fehlgeschlagen."))
    return problems
