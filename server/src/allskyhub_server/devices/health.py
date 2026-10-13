"""Is the camera all right? (roadmap C14)

Judged from what the camera last reported (SPEC §6.3 status, frame info) and whether it
is connected. The camera page shows the result; the notifier reports each problem once
when it appears (and once when it is gone).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Device, SkySample

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


def reboot_required(device: Device) -> bool:
    """SPEC §6.3, §8 ``reboot_required``: system updates wait for the camera's reboot,
    which it does by day on its own."""
    return (device.last_status or {}).get("reboot_required") is True


def paused(device: Device) -> bool:
    """SPEC §4.1, §6.3 ``capture_paused``: the owner paused capture on the camera."""
    return (device.last_status or {}).get("capture_paused") is True


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
    # SPEC §4.1: the owner paused capture; missing pictures are expected then.
    if not paused(device) and (captured is None or now - captured > stale):
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
    alpaca = status.get("alpaca_error")  # SPEC §6.3, §7.5
    if isinstance(alpaca, str) and alpaca:
        problems.append(Problem("alpaca", "warn", _alpaca_text(alpaca, settings)))
    return problems


def _alpaca_text(error: str, settings: dict[str, Any]) -> str:
    if error == "port_in_use":
        port = settings.get("alpaca_port")
        which = f"Port {port}" if isinstance(port, int) else "Der Alpaca-Port"
        return (
            f"Alpaca-Port belegt: {which} oder UDP 32227 wird schon von einem anderen "
            "Programm genutzt (etwa ASCOM Remote). In den Einstellungen einen anderen "
            "Port wählen."
        )
    return f"Die Alpaca-Schnittstelle meldet einen Fehler ({error[:40]})."


# Roadmap C12: the forecast says clear, the camera has seen next to nothing for this long.
DOME_WINDOW = dt.timedelta(hours=2)
DOME_MIN_SAMPLES = 6
DOME_CAMERA_CLOUDY = 0.8
DOME_FORECAST_CLEAR = 20  # % cloud cover


def dome_hint(
    samples: Sequence[tuple[dt.datetime, float | None]],
    forecast: Mapping[dt.datetime, int],
    now: dt.datetime,
) -> Problem | None:
    """Dew, frost or dirt on the dome: the camera's sky measurements (time, cloud cover) of
    the last two hours all say overcast while the forecast for those hours says clear."""
    recent = [(t, c) for t, c in samples if now - t <= DOME_WINDOW and c is not None]
    if len(recent) < DOME_MIN_SAMPLES:
        return None
    if any(c < DOME_CAMERA_CLOUDY for _, c in recent):
        return None
    hours = {t.replace(minute=0, second=0, microsecond=0) for t, _ in recent}
    if not hours or any(forecast.get(h, 100) > DOME_FORECAST_CLEAR for h in hours):
        return None
    return Problem(
        "dome",
        "warn",
        "Die Vorhersage meldet klaren Himmel, die Kamera sieht seit zwei Stunden fast "
        "keine Sterne: Kuppel beschlagen, vereist oder verschmutzt?",
    )


async def full_check(
    db: AsyncSession,
    device: Device,
    online: bool,
    now: dt.datetime,
    forecast: ForecastCache | None,
) -> list[Problem]:
    """``check`` plus the dome hint, which needs the sky samples and the forecast."""
    problems = check(device, online, now)
    if not online or forecast is None or (device.last_frame or {}).get("mode") != "night":
        return problems
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return problems
    rows = await db.execute(
        select(SkySample.at, SkySample.cloud_cover).where(
            SkySample.device_id == device.id, SkySample.at >= now - DOME_WINDOW
        )
    )
    samples = [(at, cloud) for at, cloud in rows.all()]
    hours = {h.at: h.clouds for h in await forecast.get(float(lat), float(lon))}
    hint = dome_hint(samples, hours, now)
    return [*problems, hint] if hint else problems
