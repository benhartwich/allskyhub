"""Camera check for the local UI (SPEC §7): one traffic light per thing that can go wrong,
with what to do about it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from allskyhub_protocol import EnvStatus, FrameInfo, HeaterStatus, LightningSensorStatus


@dataclass(frozen=True)
class CheckFacts:
    """Where the check reads from; each is called once per request."""

    power: Callable[[], list[str] | None]
    time_trusted: Callable[[], bool]
    disk_free_pct: Callable[[], float | None]
    last_frame: Callable[[], FrameInfo | None]
    paused: Callable[[], bool]
    calibration: Callable[[], dict[str, Any]] | None = None
    hub: Callable[[], tuple[bool, bool] | None] | None = None  # (paired, connected)
    # SPEC §7.11: the weather sensor (None while off) and the heater (mode, pin, state).
    env: Callable[[], EnvStatus | None] | None = None
    heater: Callable[[], tuple[str, int | None, HeaterStatus | None]] | None = None
    # SPEC §7.12: the lightning sensor, None while off.
    lightning: Callable[[], LightningSensorStatus | None] | None = None


_HEATER_REASONS = {
    "dew": "nahe am Taupunkt",
    "humidity": "hohe Luftfeuchte",
    "night": "nachts, ohne Sensor",
    "on": "immer an",
    "too_warm": "zu warm",
}


def _num(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def weather_checks(f: CheckFacts) -> list[dict[str, str]]:
    """The weather sensor and the dome heater (SPEC §7.11)."""
    out: list[dict[str, str]] = []
    env = f.env() if f.env is not None else None
    if env is not None:
        if env.error is not None:
            why = (
                f"Wettersensor antwortet nicht ({env.error}): Verkabelung, "
                "Adresse 0x76/0x77 und I²C prüfen."
            )
            out.append(_item("weather", "fail", why))
        elif env.at is None or env.temperature_c is None:
            out.append(_item("weather", "warn", "Wettersensor: noch keine Messung."))
        else:
            parts = [f"{_num(env.temperature_c)} °C"]
            if env.humidity_pct is not None:
                parts.append(f"{env.humidity_pct:.0f} % Feuchte")
            if env.dewpoint_c is not None:
                parts.append(f"Taupunkt {_num(env.dewpoint_c)} °C")
            if env.pressure_hpa is not None:
                parts.append(f"{env.pressure_hpa:.0f} hPa")
            out.append(_item("weather", "ok", ", ".join(parts) + "."))
    if f.heater is not None:
        mode, pin, state = f.heater()
        if mode == "off":
            pass
        elif pin is None:
            no_pin = "Kein GPIO-Pin eingestellt: die Heizung wird nicht geschaltet."
            out.append(_item("heater", "warn", no_pin))
        elif state is not None and state.on:
            why = _HEATER_REASONS.get(state.reason, state.reason)
            out.append(_item("heater", "ok", f"Heizung an ({why}), GPIO {pin}."))
        else:
            why = " (zu warm)" if state is not None and state.reason == "too_warm" else ""
            out.append(_item("heater", "ok", f"Heizung aus{why}, GPIO {pin}."))
    ls = f.lightning() if f.lightning is not None else None
    if ls is not None:
        out.append(_lightning_item(ls))
    return out


_LIGHTNING_ERRORS = {
    "no_pin": "Kein Interrupt-Pin eingestellt (oder derselbe wie die Heizung).",
    "not_found": "Blitzsensor antwortet nicht: Verkabelung, Adresse und I²C prüfen.",
    "no_interrupt": "Der Interrupt-Pin bleibt still: Verkabelung von IRQ und GPIO prüfen.",
}


def _lightning_item(ls: LightningSensorStatus) -> dict[str, str]:
    if ls.error is not None:
        why = _LIGHTNING_ERRORS.get(ls.error, f"Blitzsensor gestört ({ls.error}).")
        return _item("lightning_sensor", "fail", why)
    if ls.at is None:
        return _item("lightning_sensor", "warn", "Blitzsensor startet.")
    if not ls.strikes_30min:
        return _item("lightning_sensor", "ok", "Bereit, keine Blitze in den letzten 30 min.")
    near = f", der nächste {ls.nearest_km:.0f} km entfernt" if ls.nearest_km is not None else ""
    return _item("lightning_sensor", "ok", f"{ls.strikes_30min} Blitze in 30 min{near}.")


def _item(cid: str, state: str, detail: str) -> dict[str, str]:
    return {"id": cid, "state": state, "detail": detail}


def run_checks(f: CheckFacts, now: datetime) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    power = f.power()
    if power is None:
        out.append(_item("power", "ok", "Kein Raspberry Pi: nicht prüfbar."))
    elif any(not p.endswith("_seen") for p in power):
        out.append(
            _item(
                "power",
                "fail",
                "Jetzt Unterspannung oder Drosselung: stärkeres "
                "Netzteil oder kürzeres Kabel verwenden.",
            )
        )
    elif power:
        out.append(
            _item(
                "power",
                "warn",
                "Seit dem Start gab es Unterspannung oder Drosselung. Netzteil und Kabel prüfen.",
            )
        )
    else:
        out.append(_item("power", "ok", "Stromversorgung in Ordnung."))
    out.append(
        _item("clock", "ok", "Uhrzeit synchronisiert.")
        if f.time_trusted()
        else _item(
            "clock",
            "warn",
            "Uhrzeit nicht synchronisiert: Internetzugang (NTP) prüfen. "
            "Meteore werden ohne sichere Zeit nicht trianguliert.",
        )
    )
    free = f.disk_free_pct()
    if free is None:
        out.append(_item("disk", "warn", "Freier Speicher unbekannt."))
    elif free < 10:
        out.append(
            _item(
                "disk",
                "fail",
                f"Nur {free:.0f} % frei: Aufbewahrungsdauer senken oder größere Karte.",
            )
        )
    elif free < 20:
        out.append(
            _item("disk", "warn", f"{free:.0f} % frei, alte Nächte werden bald früher gelöscht.")
        )
    else:
        out.append(_item("disk", "ok", f"{free:.0f} % frei."))
    frame = f.last_frame()
    if f.paused():
        out.append(_item("frames", "warn", "Aufnahme pausiert. Unter System fortsetzen."))
    elif frame is None:
        out.append(_item("frames", "fail", "Noch kein Bild seit dem Start: Kamera und USB prüfen."))
    else:
        age = now - frame.captured_at
        limit = timedelta(seconds=frame.exposure_us / 1e6) + timedelta(minutes=10)
        out.append(
            _item("frames", "ok", "Bilder kommen.")
            if age <= limit
            else _item(
                "frames",
                "fail",
                f"Seit {int(age.total_seconds() // 60)} min kein Bild: "
                "Kamera hängt? Neustart unter System.",
            )
        )
    if f.calibration is not None:
        cal = f.calibration()
        out.append(
            _item("mask", "ok", "Himmelsmaske gelernt.")
            if cal.get("mask_learned_at")
            else _item("mask", "warn", "Himmelsmaske noch nicht gelernt: braucht eine klare Nacht.")
        )
        out.append(
            _item("orientation", "ok", "Ausrichtung bestimmt (Norden, Sternbilder).")
            if cal.get("orientation")
            else _item(
                "orientation",
                "warn",
                "Ausrichtung noch nicht bestimmt: braucht eine "
                "klare, dunkle Nacht mit sicherer Uhrzeit.",
            )
        )
    if f.hub is not None:
        state = f.hub()
        if state is None:
            pass
        elif not state[0]:
            out.append(_item("hub", "warn", "Nicht mit dem Hub gekoppelt: mit der App koppeln."))
        elif state[1]:
            out.append(_item("hub", "ok", "Mit dem Hub verbunden."))
        else:
            out.append(
                _item(
                    "hub",
                    "warn",
                    "Hub gerade nicht erreichbar. Die Kamera nimmt "
                    "weiter auf und meldet alles nach.",
                )
            )
    out += weather_checks(f)
    return out
