"""The camera page's "Himmel jetzt" tiles and the line on how well the camera knows its
orientation (drafts 2 and 4, 2026-11-13).

Only what the camera already reports in ``status`` (SPEC §6.3): ``sky`` (stars, cloud
cover, sky brightness, transparency, the 24-cell ``cloud_map``) and ``orientation``
(stars, RMS, north, solve time, §4.8). Nothing here is computed from pixels.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Literal, cast

# The agent accepts a solve up to 0.6° RMS (SPEC §4.8); below these it is good or usable.
RMS_GOOD = 0.2
RMS_USABLE = 0.4
Level = Literal["good", "usable", "rough"]
LEVEL_TEXT: dict[Level, str] = {
    "good": "gut eingemessen",
    "usable": "brauchbar eingemessen",
    "rough": "ungenau eingemessen",
}


@dataclass(frozen=True)
class Orientation:
    level: Level
    stars: int | None
    rms_deg: float | None
    north_deg: int
    solved_at: dt.datetime | None

    @property
    def text(self) -> str:
        return LEVEL_TEXT[self.level]


def orientation(status: dict[str, Any] | None) -> Orientation | None:
    """SPEC §6.3 ``status.orientation``, or None before the first solve."""
    o = (status or {}).get("orientation")
    if not isinstance(o, dict):
        return None
    o = cast(dict[str, Any], o)
    north = o.get("north_deg")
    if not isinstance(north, int | float) or not math.isfinite(north):
        return None
    rms = o.get("rms_deg")
    rms = float(rms) if isinstance(rms, int | float) and math.isfinite(rms) else None
    stars = o.get("stars")
    stars = stars if isinstance(stars, int) and not isinstance(stars, bool) else None
    if rms is None:
        level: Level = "usable"
    elif rms <= RMS_GOOD:
        level = "good"
    elif rms <= RMS_USABLE:
        level = "usable"
    else:
        level = "rough"
    return Orientation(level, stars, rms, round(north) % 360, _time(o.get("solved_at")))


@dataclass(frozen=True)
class Cell:
    path: str  # SVG path in a -100..100 box, north up, east left (like the image)
    shade: int | None  # cloud cover in tenths 0..10, None: not judgeable
    title: str


@dataclass(frozen=True)
class SkyNow:
    at: dt.datetime | None
    stars: int | None
    cloud_pct: int | None
    sqm: float | None
    transparency: float | None
    cells: list[Cell] = field(default_factory=lambda: list[Cell]())

    @property
    def empty(self) -> bool:
        return (
            self.stars is None
            and self.cloud_pct is None
            and self.sqm is None
            and self.transparency is None
            and not self.cells
        )


SECTORS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")
BANDS = ("15–40°", "40–65°", "65–90°")
# Outer and inner radius of the three altitude bands in the drawing (horizon at 80).
RADII = ((80.0, 53.0), (53.0, 27.0), (27.0, 0.0))


def sky_now(status: dict[str, Any] | None) -> SkyNow | None:
    """SPEC §6.3 ``status.sky``, or None before the first measured frame."""
    sky = (status or {}).get("sky")
    if not isinstance(sky, dict):
        return None
    sky = cast(dict[str, Any], sky)
    cover = _number(sky.get("cloud_cover"))
    stars = sky.get("stars")
    now = SkyNow(
        at=_time(sky.get("at")),
        stars=stars if isinstance(stars, int) and not isinstance(stars, bool) else None,
        cloud_pct=None if cover is None else round(min(max(cover, 0.0), 1.0) * 100),
        sqm=_number(sky.get("sqm_mag")),
        transparency=_number(sky.get("transparency")),
        cells=_cells(sky.get("cloud_map")),
    )
    return None if now.empty else now


def _cells(raw: Any) -> list[Cell]:
    """The 24 cells of ``cloud_map`` (index = 8·band + sector, sector 0 north, 2 east),
    drawn as seen from below like the image: east is left. Empty without a valid map or
    when no cell has a value."""
    if not isinstance(raw, list):
        return []
    values = cast(list[Any], raw)
    if len(values) != 24:
        return []
    cells: list[Cell] = []
    for i, v in enumerate(values):
        band, sector = divmod(i, 8)
        value = _number(v)
        shade = None if value is None else round(min(max(value, 0.0), 1.0) * 10)
        outer, inner = RADII[band]
        # Image angle, clockwise from up: azimuth mirrored (east to the left).
        a0, a1 = -(sector * 45.0 - 22.5), -(sector * 45.0 + 22.5)
        where = f"{SECTORS[sector]}, {BANDS[band]}"
        what = "nicht beurteilbar" if shade is None else f"{shade * 10} % bewölkt"
        cells.append(Cell(_sector_path(outer, inner, a0, a1), shade, f"{where}: {what}"))
    return cells if any(c.shade is not None for c in cells) else []


def _point(r: float, angle: float) -> str:
    a = math.radians(angle)
    return f"{r * math.sin(a):.1f} {-r * math.cos(a):.1f}"


def _sector_path(outer: float, inner: float, a0: float, a1: float) -> str:
    """A ring sector from image angle a0 to a1 (a1 < a0: drawn counter-clockwise)."""
    start, end = _point(outer, a0), _point(outer, a1)
    if inner <= 0:
        return f"M{start} A{outer:g} {outer:g} 0 0 0 {end} L0 0 Z"
    return (
        f"M{start} A{outer:g} {outer:g} 0 0 0 {end} "
        f"L{_point(inner, a1)} A{inner:g} {inner:g} 0 0 1 {_point(inner, a0)} Z"
    )


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        return None
    return float(v)


def _time(v: Any) -> dt.datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = dt.datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=dt.UTC)
