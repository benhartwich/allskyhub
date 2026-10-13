"""Constellations over a camera's image on the hub (roadmap H1): reads the lens and the
place from the camera's status; the drawing itself is shared with the camera
(``allskyhub_sky``)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from allskyhub_protocol import Orientation
from allskyhub_server.devices.aircraft import CREDIT, TRAIL, Tracks
from allskyhub_sky import Overlay, overlay, project, usable


def orientation(status: dict[str, Any] | None) -> Orientation | None:
    raw = (status or {}).get("orientation")
    if not isinstance(raw, dict):
        return None
    try:
        found = Orientation.model_validate(raw)
    except ValidationError:
        return None
    return found if usable(found.lens) else None


def build(status: dict[str, Any] | None, at: dt.datetime) -> Overlay | None:
    """The overlay for an image taken ``at``; None without a usable lens or place."""
    found = orientation(status)
    settings: dict[str, Any] = (status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if found is None or found.lens is None:
        return None
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return overlay(found.lens, found.north_deg, found.mirrored, float(lat), float(lon), at)


def image_time(device_last_frame: dict[str, Any] | None, fallback: dt.datetime) -> dt.datetime:
    """When the shown image was taken (``captured_at`` of the last frame), else ``fallback``."""
    raw = (device_last_frame or {}).get("captured_at")
    if isinstance(raw, str):
        try:
            at = dt.datetime.fromisoformat(raw)
        except ValueError:
            return fallback
        return at if at.tzinfo else at.replace(tzinfo=dt.UTC)
    return fallback


@dataclass(frozen=True)
class Aircraft:
    x: float
    y: float
    text: str
    trail: str  # SVG path of the last minute, or ""


@dataclass(frozen=True)
class AircraftOverlay:
    """Roadmap H3 (SPEC §7.8): the aircraft above the horizon at the image's time."""

    width: int
    height: int
    items: list[Aircraft] = field(default_factory=lambda: list[Aircraft]())
    credit: str = CREDIT

    @property
    def font(self) -> float:
        return round(self.width / 80, 1)


TRAIL_STEP = dt.timedelta(seconds=10)


def _altitude(metres: float | None) -> str:
    return "" if metres is None else f" · {metres / 1000:.1f} km".replace(".", ",")


def build_aircraft(
    status: dict[str, Any] | None, at: dt.datetime, tracks: Tracks
) -> AircraftOverlay | None:
    """None without a usable lens or place; an empty overlay when no aircraft is up."""
    found = orientation(status)
    settings: dict[str, Any] = (status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if found is None or found.lens is None:
        return None
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    lens = found.lens

    def pixel(elevation: float, azimuth: float) -> tuple[float, float]:
        return project(elevation, azimuth, lens, found.north_deg, found.mirrored)

    result = AircraftOverlay(lens.width, lens.height)
    trails: dict[str, list[tuple[float, float]]] = {}
    t = at - TRAIL
    while t < at:
        for plane in tracks.planes(t, float(lat), float(lon)):
            trails.setdefault(plane.hex, []).append(pixel(plane.elevation, plane.azimuth))
        t += TRAIL_STEP
    for plane in tracks.planes(at, float(lat), float(lon)):
        x, y = pixel(plane.elevation, plane.azimuth)
        points = [*trails.get(plane.hex, []), (x, y)]
        trail = "M" + "L".join(f"{px:.1f} {py:.1f}" for px, py in points) if len(points) > 1 else ""
        result.items.append(Aircraft(x, y, plane.name + _altitude(plane.alt_m), trail))
    return result
