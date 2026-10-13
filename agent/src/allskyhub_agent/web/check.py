"""Camera check for the local UI (SPEC §7): one traffic light per thing that can go wrong,
with what to do about it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from allskyhub_protocol import FrameInfo


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
    return out
