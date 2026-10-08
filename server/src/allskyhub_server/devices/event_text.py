"""German labels for detections (SPEC §6.4), for the web UI."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from allskyhub_server.models import EventRecord

EVENT_TITLES = {
    "meteor": "Meteor",
    "lightning": "Blitz",
    "aurora": "Polarlicht",
    "nlc": "Leuchtende Nachtwolken",
    "satellite": "Satellit",
    "clouds": "Wolken",
    "sky_quality": "Himmelsqualität",
}
# SPEC §6.4: direction_deg is the trail's axis in the image (0..180, 0 = up), no sense of travel.
_AXES = (
    "senkrecht",
    "diagonal, oben rechts – unten links",
    "waagrecht",
    "diagonal, oben links – unten rechts",
)


COMPASS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")


def compass(azimuth: float) -> str:
    """Azimuth (0 = north, clockwise) as one of eight German compass points."""
    return COMPASS[round((azimuth % 360) / 45) % 8]


def orientation_line(status: dict[str, Any] | None) -> str | None:
    """SPEC §4.8 ``status.orientation``: where north is in the image and how well it is
    known, or None before the first calibration."""
    o = (status or {}).get("orientation")
    if not isinstance(o, dict):
        return None
    o = cast(dict[str, Any], o)
    north = o.get("north_deg")
    if not isinstance(north, int | float):
        return None
    parts = [f"Norden bei {round(north) % 360}° im Bild"]
    if isinstance(stars := o.get("stars"), int):
        parts.append(f"kalibriert auf {stars} Sternen")
    if isinstance(rms := o.get("rms_deg"), int | float):
        parts.append(f"±{rms:.1f}°".replace(".", ","))
    if isinstance(solved := o.get("solved_at"), str):
        try:
            day = dt.datetime.fromisoformat(solved)
            parts.append(f"{day.day}.{day.month}.")
        except ValueError:
            pass
    return " · ".join(parts)


UPDATE_STATES = {
    "up_to_date": "aktuell",
    "downloading": "Update {v} wird geladen",
    "waiting": "Update {v} geladen, wird tagsüber installiert",
    "installing": "Update {v} wird installiert",
    "installed": "Update {v} installiert",
    "rolled_back": "Update {v} zurückgerollt",
    "failed": "Update {v} fehlgeschlagen",
}
UPDATE_CODES = {
    "unhealthy": "die neue Version lief nicht sauber",
    "checksum": "Prüfsumme falsch",
    "download": "Download fehlgeschlagen",
    "bad_bundle": "Paket beschädigt",
    "bad_signature": "Signatur ungültig",
    "bad_manifest": "Update-Beschreibung ungültig",
    "no_space": "zu wenig Speicher",
}


def update_line(status: dict[str, Any] | None) -> tuple[str, bool] | None:
    """Roadmap #7 ``status.update``: German text and whether it is a warning, or None
    before the updater has run once."""
    u = (status or {}).get("update")
    if not isinstance(u, dict):
        return None
    u = cast(dict[str, Any], u)
    state = u.get("state")
    if not isinstance(state, str) or state not in UPDATE_STATES:
        return None
    text = UPDATE_STATES[state].format(v=u.get("version") or "")
    code = u.get("code")
    if isinstance(code, str) and code:
        text += f" ({UPDATE_CODES.get(code, code)})"
    if isinstance(at := u.get("at"), str) and state not in ("up_to_date", "downloading"):
        try:
            when = dt.datetime.fromisoformat(at)
            text += f", {when.day}.{when.month}."
        except ValueError:
            pass
    return text, state in ("rolled_back", "failed")


def event_rows(event: EventRecord) -> list[tuple[str, str]]:
    """Label/value rows: meteor keys as in SPEC §6.4, unknown keys as they come."""
    rows = [("Sicherheit", f"{round(event.confidence * 100)} %")]
    seconds = (event.end - event.start).total_seconds()
    if seconds > 0:
        rows.append(("Dauer", f"{seconds:.1f} s".replace(".", ",")))
    data: dict[str, Any] = dict(event.data)
    if event.kind == "meteor":
        if isinstance(length := data.pop("length_px", None), int | float):
            rows.append(("Spurlänge", f"{round(length)} px"))
        if isinstance(peak := data.pop("peak", None), int | float):
            rows.append(("Helligkeit", f"{round(peak * 100)} %"))
        if isinstance(frames := data.pop("frames", None), int | float):
            rows.append(("Bilder", str(round(frames))))
        if isinstance(direction := data.pop("direction_deg", None), int | float):
            axis = round((direction % 180) / 45) % 4
            rows.append(("Richtung", f"{round(direction)}° ({_AXES[axis]})"))
        if isinstance(shower := data.pop("shower", None), str) and shower:
            rows.append(("Meteorstrom", shower))
    if event.kind == "lightning":
        if isinstance(area := data.pop("area_frac", None), int | float):
            rows.append(("Erhellter Himmel", f"{round(area * 100)} %"))
        if isinstance(peak := data.pop("peak", None), int | float):
            rows.append(("Aufhellung", f"{round(peak * 100)} %"))
        if isinstance(flashes := data.pop("storm_flashes", None), int | float):
            rows.append(("Blitze in 30 Min.", str(round(flashes))))
        data.pop("storm", None)  # shown as the storm the flash belongs to
    if event.kind in ("aurora", "nlc"):
        if isinstance(index := data.pop("peak_index", None), int | float):
            rows.append(("Stärke", f"{round(index)} %"))
        if isinstance(green := data.pop("green", None), int | float):
            rows.append(("Grünanteil", f"{green:.0f}".replace(".", ",")))
        if isinstance(blue := data.pop("blue", None), int | float):
            rows.append(("Blauanteil", f"{blue:.0f}".replace(".", ",")))
        if isinstance(frames := data.pop("frames", None), int | float):
            rows.append(("Bilder", str(round(frames))))
        if isinstance(direction := data.pop("direction_deg", None), int | float):
            # Image-relative (0 = up), not a compass bearing: no N/E/S/W without calibration.
            rows.append(("Bildrichtung", f"{round(direction) % 360}°"))
        if data.pop("ongoing", None) is True:
            rows.append(("Status", "läuft noch"))
    data.pop("image_rev", None)
    # With a calibrated camera (SPEC §4.8) the agent adds the real direction on the sky.
    if isinstance(azimuth := data.pop("azimuth_deg", None), int | float):
        rows.append(("Himmelsrichtung", f"{round(azimuth) % 360}° ({compass(azimuth)})"))
    if isinstance(altitude := data.pop("altitude_deg", None), int | float):
        rows.append(("Höhe über dem Horizont", f"{round(altitude)}°"))
    rows.extend((key, str(value)) for key, value in data.items() if value is not None)
    return rows
