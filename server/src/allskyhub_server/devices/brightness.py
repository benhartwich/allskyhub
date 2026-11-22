"""Sky brightness per sky cell (SPEC §6.3 ``brightness_map``, roadmap L37).

A single map is skewed by the Milky Way and passing clouds (SPEC §6.3, known limits), so
the hub shows only the night map: per cell the median over the night's frames in which
that cell was clear (its ``cloud_map`` cell below ``CLEAR_CELL``) and the moon was below
the horizon. The map is kept in ``NightStat`` and outlives the samples.

Cells: 16 azimuth sectors of 22.5° (sector k from k·22.5°, north through east) times the
three altitude bands of ``cloud_map``; index = 16·band + sector.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices.sky_overlay import orientation
from allskyhub_server.models import NightStat, SkySample
from allskyhub_sky import project
from allskyhub_sky.astro import moon_altitude

SECTORS = 16
BANDS = ((15.0, 40.0), (40.0, 65.0), (65.0, 90.0))  # SPEC §6.3, as cloud_map
CELLS = SECTORS * len(BANDS)
CLEAR_CELL = 0.1  # cloud_map value below which a cell counts as clear
MIN_FRAMES = 6  # clear moonless frames per cell for its night value
# The colour scale is fixed so nights and cameras compare: city sky to a dark site.
SCALE = (17.0, 22.0)
# Sequential blue ramp (dataviz reference palette, steps 100..650): light = bright sky,
# dark = dark sky.
RAMP = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#104281")
ARC_STEP = 4  # points per sector edge on a curved border


def cloud_cell(index: int) -> int:
    """The ``cloud_map`` cell a brightness cell lies in (SPEC §6.3)."""
    band, sector = divmod(index, SECTORS)
    return 8 * band + ((sector + 1) // 2) % 8


def cell_bounds(index: int) -> tuple[float, float, float, float]:
    """(alt_low, alt_high, az_start, az_end) in degrees."""
    band, sector = divmod(index, SECTORS)
    low, high = BANDS[band]
    return low, high, sector * 22.5, (sector + 1) * 22.5


def _floats(raw: Any, length: int) -> list[float | None] | None:
    if not isinstance(raw, list) or len(cast(list[Any], raw)) != length:
        return None
    out: list[float | None] = []
    for v in cast(list[Any], raw):
        out.append(float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None)
    return out


@dataclass(frozen=True)
class NightMap:
    values: list[float | None]  # CELLS values, mag/arcsec²
    flat: str | None  # SPEC §6.3 brightness_map_flat of the frames used
    frames: int  # frames that gave at least one cell


def night_map(samples: Iterable[SkySample], lat: float, lon: float) -> NightMap | None:
    """The night's map from its samples; None if no cell has ``MIN_FRAMES`` values.

    Only frames with the night's most common correction (``flat``) are used, so a night
    in which the twilight flat arrived does not mix both."""
    usable: list[tuple[SkySample, list[float | None], list[float | None]]] = []
    for s in samples:
        values = _floats(s.brightness_map, CELLS)
        clouds = _floats(s.cloud_map, 24)
        if values is None or clouds is None or not any(v is not None for v in values):
            continue
        usable.append((s, values, clouds))
    if not usable:
        return None
    flat = Counter(s.brightness_map_flat for s, _, _ in usable).most_common(1)[0][0]
    per_cell: list[list[float]] = [[] for _ in range(CELLS)]
    frames = 0
    for s, values, clouds in usable:
        if s.brightness_map_flat != flat or moon_altitude(s.at, lat, lon) >= 0:
            continue
        used = False
        for i, v in enumerate(values):
            cloud = clouds[cloud_cell(i)]
            if v is not None and cloud is not None and cloud < CLEAR_CELL:
                per_cell[i].append(v)
                used = True
        frames += used
    result: list[float | None] = [
        round(statistics.median(vs), 2) if len(vs) >= MIN_FRAMES else None for vs in per_cell
    ]
    if all(v is None for v in result):
        return None
    return NightMap(result, flat, frames)


def color(value: float) -> str:
    """The ramp's colour for a value on the fixed ``SCALE``, interpolated between its
    steps (a tenth of a magnitude still shows)."""
    low, high = SCALE
    t = min(max((value - low) / (high - low), 0.0), 1.0) * (len(RAMP) - 1)
    k = min(int(t), len(RAMP) - 2)
    a, b = RAMP[k], RAMP[k + 1]
    f = t - k
    mixed = (round(int(a[i : i + 2], 16) * (1 - f) + int(b[i : i + 2], 16) * f) for i in (1, 3, 5))
    return "#" + "".join(f"{c:02x}" for c in mixed)


def _cell_path(index: int, point: Callable[[float, float], tuple[float, float]]) -> str:
    """A closed SVG path around a cell: the lower arc, then the upper arc backwards."""
    low, high, az0, az1 = cell_bounds(index)
    azs = [az0 + (az1 - az0) * k / ARC_STEP for k in range(ARC_STEP + 1)]
    ring = [point(low, az) for az in azs] + [point(high, az) for az in reversed(azs)]
    return "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in ring) + "Z"


@dataclass(frozen=True)
class Cell:
    index: int
    path: str
    value: float
    color: str
    label: str  # "NNO · 40–65°: 20,85"


DIRECTIONS = (
    "N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)  # fmt: skip
DIRECTIONS_EN = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)  # fmt: skip


def cells(
    values: Sequence[float | None],
    point: Callable[[float, float], tuple[float, float]],
    english: bool = False,
) -> list[Cell]:
    """The measured cells as paths through ``point(alt, az) -> (x, y)``."""
    out: list[Cell] = []
    names = DIRECTIONS_EN if english else DIRECTIONS
    for i, v in enumerate(values):
        if v is None:
            continue
        low, high, _, _ = cell_bounds(i)
        number = f"{v:.2f}" if english else f"{v:.2f}".replace(".", ",")
        label = f"{names[i % SECTORS]} · {low:.0f}–{high:.0f}°: {number}"
        out.append(Cell(i, _cell_path(i, point), v, color(v), label))
    return out


# The polar chart of the web pages: zenith in the middle, the horizon (15°) at the rim,
# north up and east on the left, as one looks up at the sky (and as the image shows it).
CHART_SIZE = 240
CHART_RADIUS = 104.0


def chart_point(alt: float, az: float) -> tuple[float, float]:
    r = CHART_RADIUS * (90.0 - alt) / (90.0 - BANDS[0][0])
    c = CHART_SIZE / 2
    a = math.radians(az)
    return c - r * math.sin(a), c - r * math.cos(a)


@dataclass(frozen=True)
class Chart:
    night_id: str
    cells: list[Cell]
    flat: str | None
    english: bool = False
    empty: tuple[str, ...] = ()  # paths of the cells without a value
    size: int = CHART_SIZE

    @property
    def rings(self) -> list[float]:
        """Radii of the band borders, for the grid."""
        return [CHART_RADIUS * (90.0 - b[1]) / 75.0 for b in BANDS[:2]] + [CHART_RADIUS]

    @property
    def legend(self) -> list[dict[str, str]]:
        low, high = SCALE
        steps = len(RAMP)
        return [
            {"color": c, "value": f"{low + (high - low) * k / (steps - 1):.1f}"}
            for k, c in enumerate(RAMP)
        ]

    @property
    def cardinals(self) -> list[tuple[float, float, str]]:
        east = "E" if self.english else "O"
        return [
            (*chart_point(9.0, az), letter)
            for az, letter in ((0.0, "N"), (90.0, east), (180.0, "S"), (270.0, "W"))
        ]

    @property
    def darkest(self) -> Cell:
        return max(self.cells, key=lambda c: c.value)

    @property
    def brightest(self) -> Cell:
        return min(self.cells, key=lambda c: c.value)


def chart(night_id: str, values: Any, flat: str | None, english: bool = False) -> Chart | None:
    parsed = _floats(values, CELLS)
    if parsed is None:
        return None
    found = cells(parsed, chart_point, english)
    if not found:
        return None
    empty = tuple(_cell_path(i, chart_point) for i, v in enumerate(parsed) if v is None)
    return Chart(night_id, found, flat, english, empty)


RECENT_NIGHTS = 30  # the newest night map no older than this is shown


async def latest_chart(
    db: AsyncSession, device_id: str, today: dt.date, english: bool = False
) -> Chart | None:
    """The newest night map of the last ``RECENT_NIGHTS`` nights, as the pages' chart."""
    since = (today - dt.timedelta(days=RECENT_NIGHTS)).strftime("%Y%m%d")
    row = (
        await db.execute(
            select(NightStat.night_id, NightStat.brightness_map, NightStat.brightness_map_flat)
            .where(
                NightStat.device_id == device_id,
                NightStat.night_id >= since,
                NightStat.brightness_map.is_not(None),
            )
            .order_by(NightStat.night_id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return chart(row[0], row[1], row[2], english)


async def image_layer(
    db: AsyncSession,
    device_id: str,
    status: dict[str, Any] | None,
    today: dt.date,
    english: bool = False,
) -> dict[str, Any] | None:
    """The newest night map over the live image (the app's layer), in pixels of the lens's
    frame like the other layers; None without a map or a usable lens."""
    found = orientation(status)
    if found is None or found.lens is None:
        return None
    newest = await latest_chart(db, device_id, today, english)
    if newest is None:
        return None
    lens = found.lens

    def pixel(alt: float, az: float) -> tuple[float, float]:
        return project(alt, az, lens, found.north_deg, found.mirrored)

    values: list[float | None] = [None] * CELLS
    for c in newest.cells:
        values[c.index] = c.value
    return {
        "night_id": newest.night_id,
        "flat": newest.flat,
        "scale": list(SCALE),
        "cells": [
            {"path": c.path, "value": c.value, "color": c.color, "label": c.label}
            for c in cells(values, pixel, english)
        ],
    }
