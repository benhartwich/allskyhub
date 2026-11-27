"""Is the camera all right? (roadmap C14)

Judged from what the camera last reported (SPEC §6.3 status, frame info) and whether it
is connected. The camera page shows the result; the notifier reports each problem once
when it appears (and once when it is gone).
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import star_size
from allskyhub_server.devices.night_summary import CLEAR_BELOW
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.i18n import tr
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
    push: bool = True  # False: shown on the page and in the app, never sent as a notice


def _age(delta: dt.timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 120:
        return tr(f"{minutes} Minuten", f"{minutes} minutes")
    hours = minutes // 60
    if hours < 48:
        return tr(f"{hours} Stunden", f"{hours} hours")
    return tr(f"{hours // 24} Tagen", f"{hours // 24} days")


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


def waiting(device: Device) -> tuple[str, str | None] | None:
    """SPEC §6.3 ``waiting``: (reason, message) while the camera takes no frames yet."""
    raw = (device.last_status or {}).get("waiting")
    if not isinstance(raw, dict):
        return None
    found = cast(dict[str, Any], raw)
    reason = found.get("reason")
    if reason not in ("location", "camera"):
        return None
    message = found.get("message")
    return str(reason), str(message)[:200] if isinstance(message, str) else None


def paused(device: Device) -> bool:
    """SPEC §4.1, §6.3 ``capture_paused``: the owner paused capture on the camera."""
    return (device.last_status or {}).get("capture_paused") is True


# SPEC §7.11, §7.12: why a sensor gives no values.
def _sensor_error(code: str) -> str:
    return {
        "not_found": tr(
            "nicht gefunden, Anschluss und Adresse prüfen",
            "not found, check the wiring and the address",
        ),
        "no_interrupt": tr(
            "die Interrupt-Leitung bleibt stumm, den GPIO-Pin prüfen",
            "the interrupt line stays silent, check the GPIO pin",
        ),
        "bad_data": tr("liefert unbrauchbare Werte", "gives unusable values"),
    }.get(code, code[:40])


# SPEC §6.3 ``checks``: the camera's own check (§7). These three make the setup card; any
# other ``fail`` is a warning, unless the hub's own check above already says it.
SETUP_STEPS = {
    "frames": "Bilder kommen an",
    "mask": "Himmelsmaske gelernt",
    "orientation": "Ausrichtung (Norden) bestimmt",
}
SETUP_STEPS_EN = {
    "frames": "Images arrive",
    "mask": "Sky mask learnt",
    "orientation": "Orientation (north) found",
}
HUB_SAYS = {
    "disk": "disk",
    "clock": "clock",
    "weather": "env",
    "lightning_sensor": "lightning_sensor",
}


@dataclass(frozen=True)
class Step:
    title: str
    state: Literal["ok", "warn", "fail"]
    detail: str


def _checks(device: Device) -> list[dict[str, Any]]:
    raw = (device.last_status or {}).get("checks")
    if not isinstance(raw, list):
        return []
    items = [cast(dict[str, Any], c) for c in cast(list[object], raw) if isinstance(c, dict)]
    return [
        c for c in items
        if isinstance(c.get("id"), str) and c.get("state") in ("ok", "warn", "fail")
        and isinstance(c.get("detail"), str)
    ]  # fmt: skip


def setup_steps(device: Device) -> list[Step]:
    """The setup card while frames, sky mask or orientation is not ``ok``; else empty.
    Agents without ``checks`` (null) get no card."""
    found = {c["id"]: c for c in _checks(device) if c["id"] in SETUP_STEPS}
    if not found or all(c["state"] == "ok" for c in found.values()):
        return []
    return [
        Step(tr(title, SETUP_STEPS_EN[key]), found[key]["state"], found[key]["detail"])
        for key, title in SETUP_STEPS.items()
        if key in found
    ]


def check(device: Device, online: bool, now: dt.datetime) -> list[Problem]:
    problems: list[Problem] = []
    if not online:
        seen = device.last_seen_at
        if seen is None or now - seen >= OFFLINE_AFTER:
            since = f" {tr('seit', 'for')} {_age(now - seen)}" if seen else ""
            problems.append(
                Problem(
                    "offline",
                    "error",
                    tr(f"Die Kamera ist offline{since}.", f"The camera is offline{since}."),
                )
            )
        return problems  # the rest is about a running camera

    status: dict[str, Any] = device.last_status or {}
    settings = cast(dict[str, Any], status.get("settings") or {})
    delays = [v for v in (settings.get("day_delay_s"), settings.get("night_delay_s"))
              if isinstance(v, int | float)]  # fmt: skip
    stale = max(STALE_FRAMES, dt.timedelta(seconds=3 * max(delays, default=0)))
    captured = _when((device.last_frame or {}).get("captured_at"))
    found = waiting(device)
    if found is not None:
        # SPEC §6.3 `waiting`: say why there are no pictures and what to do, instead of
        # "no new pictures".
        reason, message = found
        if reason == "location":
            problems.append(
                Problem(
                    "waiting_location",
                    "warn",
                    tr(
                        "Die Kamera wartet auf ihren Standort: Schließe die Einrichtung in "
                        "der allskyhub-App ab (Standort vom Handy übernehmen). Dann nimmt "
                        "sie innerhalb weniger Sekunden auf.",
                        "The camera waits for its location: finish the setup in the "
                        "allskyhub app (take the location from your phone). It then starts "
                        "capturing within seconds.",
                    ),
                )
            )
        else:
            problems.append(
                Problem(
                    "waiting_camera",
                    "error",
                    tr(
                        f"Die Kamera meldet einen Fehler: {message or 'unbekannt'}. Prüfe das "
                        "Kabel der Kamera (USB bzw. Flachbandkabel am Pi) und starte den Pi "
                        "neu, wenn es bleibt.",
                        f"The camera reports an error: {message or 'unknown'}. Check the "
                        "camera's cable (USB or the ribbon cable at the Pi) and restart the Pi "
                        "if it stays.",
                    ),
                )
            )
    # SPEC §4.1: the owner paused capture; missing pictures are expected then.
    elif not paused(device) and (captured is None or now - captured > stale):
        since = f" {tr('seit', 'for')} {_age(now - captured)}" if captured else ""
        problems.append(
            Problem(
                "no_frames",
                "error",
                tr(
                    f"Verbunden, aber keine neuen Bilder{since}.",
                    f"Connected, but no new pictures{since}.",
                ),
            )
        )

    disk = status.get("disk_free_pct")
    if isinstance(disk, int | float) and disk < DISK_WARN:
        level = "error" if disk < DISK_ERROR else "warn"
        problems.append(
            Problem(
                "disk",
                level,
                tr(
                    f"Nur noch {round(disk)} % Speicher frei.",
                    f"Only {round(disk)} % storage left.",
                ),
            )
        )
    sensor = status.get("sensor_temp_c")
    if isinstance(sensor, int | float) and sensor >= SENSOR_HOT:
        problems.append(
            Problem(
                "sensor_hot",
                "warn",
                tr(
                    f"Sensor sehr warm: {round(sensor)} °C.",
                    f"Sensor very warm: {round(sensor)} °C.",
                ),
            )
        )
    cpu = status.get("cpu_temp_c")
    if isinstance(cpu, int | float) and cpu >= CPU_HOT:
        problems.append(
            Problem(
                "cpu_hot",
                "warn",
                tr(
                    f"Prozessor sehr heiß: {round(cpu)} °C.",
                    f"Processor very hot: {round(cpu)} °C.",
                ),
            )
        )
    if status.get("time_trusted") is False:
        problems.append(
            Problem(
                "clock",
                "warn",
                tr(
                    "Die Uhr der Kamera ist nicht synchronisiert.",
                    "The camera's clock is not synchronized.",
                ),
            )
        )
    update = cast(dict[str, Any], status.get("update") or {})
    if update.get("state") in ("failed", "rolled_back"):
        problems.append(
            Problem(
                "update",
                "warn",
                tr(
                    "Das letzte Software-Update ist fehlgeschlagen.",
                    "The last software update failed.",
                ),
            )
        )
    for key, name in (
        ("env", tr("Klimasensor", "Weather sensor")),
        ("lightning_sensor", tr("Blitzsensor", "Lightning sensor")),
    ):
        part = status.get(key)  # SPEC §7.11, §7.12
        error = cast(dict[str, Any], part).get("error") if isinstance(part, dict) else None
        if isinstance(error, str) and error:
            problems.append(Problem(key, "warn", f"{name}: {_sensor_error(error)}."))
    alpaca = status.get("alpaca_error")  # SPEC §6.3, §7.5
    if isinstance(alpaca, str) and alpaca:
        problems.append(Problem("alpaca", "warn", _alpaca_text(alpaca, settings)))
    said = {p.key for p in problems}
    for c in _checks(device):
        if c["state"] != "fail" or c["id"] in SETUP_STEPS or HUB_SAYS.get(c["id"]) in said:
            continue
        problems.append(Problem(f"check_{c['id'][:40]}", "error", c["detail"][:300]))
    return problems


def _alpaca_text(error: str, settings: dict[str, Any]) -> str:
    if error == "port_in_use":
        port = settings.get("alpaca_port")
        which = (
            f"Port {port}" if isinstance(port, int) else tr("Der Alpaca-Port", "The Alpaca port")
        )
        return tr(
            f"Alpaca-Port belegt: {which} oder UDP 32227 wird schon von einem anderen "
            "Programm genutzt (etwa ASCOM Remote). In den Einstellungen einen anderen "
            "Port wählen.",
            f"Alpaca port in use: {which} or UDP 32227 is already used by another "
            "program (such as ASCOM Remote). Choose another port in the settings.",
        )
    return tr(
        f"Die Alpaca-Schnittstelle meldet einen Fehler ({error[:40]}).",
        f"The Alpaca interface reports an error ({error[:40]}).",
    )


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
        tr(
            "Die Vorhersage meldet klaren Himmel, die Kamera sieht seit zwei Stunden fast "
            "keine Sterne: Kuppel beschlagen, vereist oder verschmutzt?",
            "The forecast says clear sky, but the camera has seen almost no stars for two "
            "hours: is the dome fogged up, frosted or dirty?",
        ),
    )


# Draft E (approved 2026-11-15): the stars blur before they vanish. The forecast says clear,
# the camera still sees stars, but they are FACTOR times their usual size.
BLUR_WINDOW = dt.timedelta(minutes=45)
BLUR_MIN_SAMPLES = 6
# The sizes must reach back this far: not the first frames after dusk (HFR from sun -12 deg).
BLUR_SPAN = dt.timedelta(minutes=40)
DEW_GAP_C = 2.0  # air temperature this close to the dew point: name the dew


def _px(value: float) -> str:
    return tr(f"{value:.1f}".replace(".", ","), f"{value:.1f}")


def blur_hint(
    samples: Sequence[tuple[dt.datetime, float | None, float | None]],
    usual: float | None,
    forecast: Mapping[dt.datetime, int],
    now: dt.datetime,
    env: Mapping[str, Any] | None = None,
    active: bool = False,
) -> Problem | None:
    """Dew or frost starting on the dome: in the last ``BLUR_WINDOW`` the sky was clear in
    every measurement (as for a clear night, ``CLEAR_BELOW``), the star sizes (time, cloud
    cover, hfr) reach back ``BLUR_SPAN`` and each was at least ``star_size.FACTOR`` times
    the usual one, while the forecast said clear. ``active``: the hint was already given;
    with the same window it stays while their median is at least ``star_size.RELEASE``
    times the usual one."""
    if usual is None or usual <= 0:
        return None
    recent = [(t, c, h) for t, c, h in samples if now - t <= BLUR_WINDOW]
    sized = [(t, h) for t, _, h in recent if h is not None]
    if any(c is not None and c >= CLEAR_BELOW for _, c, _ in recent):
        return None
    # Always a full window of sizes, also for a hint already given: after an outage or the
    # next dusk, one large star is not enough to bring it back (review of #467).
    if len(sized) < BLUR_MIN_SAMPLES or now - min(t for t, _ in sized) < BLUR_SPAN:
        return None
    if active:
        if statistics.median(h for _, h in sized) < star_size.RELEASE * usual:
            return None
    elif any(h < star_size.FACTOR * usual for _, h in sized):
        return None
    hours = {t.replace(minute=0, second=0, microsecond=0) for t, _, _ in recent}
    if any(forecast.get(h, 100) > DOME_FORECAST_CLEAR for h in hours):
        return None
    now_px, usual_px = _px(statistics.median(h for _, h in sized)), _px(usual)
    text = tr(
        f"Die Sterne sind seit 45 Minuten deutlich unschärfer als sonst ({now_px} statt "
        f"{usual_px} px), obwohl klarer Himmel vorhergesagt ist. Beginnt die Kuppel zu "
        "beschlagen?",
        f"For 45 minutes the stars have been much less sharp than usual ({now_px} instead "
        f"of {usual_px} px) although the forecast says clear: is the dome fogging up?",
    )
    air = env.get("temperature_c") if env and not env.get("error") else None
    dew = env.get("dewpoint_c") if env and not env.get("error") else None
    if isinstance(air, int | float) and isinstance(dew, int | float) and air - dew < DEW_GAP_C:
        gap = max(float(air - dew), 0.0)
        text += tr(
            f" Die Luft am Gehäuse ist nur {_px(gap)} °C wärmer als der Taupunkt.",
            f" The air at the housing is only {_px(gap)} °C above the dew point.",
        )
    return Problem("dome_blur", "warn", text)


def focus_hint(newest_first: list[float]) -> Problem | None:
    """Draft E: the stars of the recent clear nights are much larger than before (focus
    shifted, lens loose). Not urgent: never pushed."""
    found = star_size.drift(newest_first)
    if found is None:
        return None
    now_px, before_px = _px(found[0]), _px(found[1])
    return Problem(
        "focus",
        "warn",
        tr(
            f"Seit etwa einer Woche sind die Sterne in klaren Nächten unschärfer als vorher "
            f"({now_px} statt {before_px} px). Hat sich der Fokus verstellt? Der "
            "Scharfstell-Modus auf der Seite der Kamera im Heimnetz hilft beim Nachstellen.",
            f"For about a week the stars of clear nights have been less sharp than before "
            f"({now_px} instead of {before_px} px). Has the focus shifted? The focus mode on "
            "the camera's page in the home network helps to set it again.",
        ),
        push=False,
    )


async def full_check(
    db: AsyncSession,
    device: Device,
    online: bool,
    now: dt.datetime,
    forecast: ForecastCache | None,
) -> list[Problem]:
    """``check`` plus the dome and star size hints, which need the sky samples, the
    camera's clear nights and the forecast."""
    problems = check(device, online, now)
    camera = star_size.camera_key(device)
    focus = focus_hint(
        await star_size.medians(
            db, device.id, camera, star_size.DRIFT_RECENT + star_size.DRIFT_BEFORE
        )
    )
    if focus:
        problems.append(focus)
    if not online or forecast is None or (device.last_frame or {}).get("mode") != "night":
        return problems
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return problems
    rows = (
        await db.execute(
            select(SkySample.at, SkySample.cloud_cover, SkySample.hfr, SkySample.night_id)
            .where(SkySample.device_id == device.id, SkySample.at >= now - DOME_WINDOW)
            .order_by(SkySample.at)
        )
    ).all()
    hours = {h.at: h.clouds for h in await forecast.get(float(lat), float(lon))}
    hint = dome_hint([(at, cloud) for at, cloud, _, _ in rows], hours, now)
    if hint:
        return [*problems, hint]  # the later stage of the same: said once
    sized = [(at, cloud, hfr) for at, cloud, hfr, _ in rows]
    if not any(h is not None for _, _, h in sized):
        return problems
    usual = await star_size.usual(db, device, rows[-1][3])
    env = (device.last_status or {}).get("env")
    blur = blur_hint(
        sized,
        usual,
        hours,
        now,
        cast(dict[str, Any], env) if isinstance(env, dict) else None,
        active="dome_blur" in (device.health_notified or []),
    )
    return [*problems, blur] if blur else problems
