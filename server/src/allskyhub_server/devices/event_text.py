"""German labels for detections (SPEC §6.4), for the web UI."""

from __future__ import annotations

from typing import Any

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
        if isinstance(frames := data.pop("frames", None), int | float):
            rows.append(("Bilder", str(round(frames))))
        if isinstance(direction := data.pop("direction_deg", None), int | float):
            # Image-relative (0 = up), not a compass bearing: no N/E/S/W without calibration.
            rows.append(("Bildrichtung", f"{round(direction) % 360}°"))
        if data.pop("ongoing", None) is True:
            rows.append(("Status", "läuft noch"))
    data.pop("image_rev", None)
    rows.extend((key, str(value)) for key, value in data.items() if value is not None)
    return rows
