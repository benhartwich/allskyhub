"""Cloud cover per sky cell from the stars that should be visible (SPEC §6.3 `cloud_map`).

24 cells: 8 azimuth sectors of 45° centred on N, NE, E, … x 3 altitude bands (15–40°,
40–65°, 65–90°), index = 8·band + sector. A cell's value is 1 - found/expected over the
catalogue stars down to magnitude 4.0 that the plate-solved lens (SPEC §4.8) puts inside
the cell and inside the sky mask; a star counts as found if a detected star lies within 1°
of where it should be. Too few expected stars, or a lit moon nearby, leave the cell null.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path

import numpy as np
import numpy.typing as npt

from allskyhub_agent.calib.solve import Solution, alt_az, sidereal_deg
from allskyhub_sky.astro import moon_illumination, moon_radec

F = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]

STARS = Path(__file__).parent.parent / "calib" / "stars_mag4.json"
SECTORS = 8
BANDS = (15.0, 40.0, 65.0, 90.0)
CELLS = SECTORS * (len(BANDS) - 1)
MIN_EXPECTED = 3
TOLERANCE_DEG = 1.0
MOON_DEG = 25.0
MOON_LIT = 0.25


@cache
def _stars() -> tuple[F, F]:
    rows: list[list[float]] = json.loads(STARS.read_text(encoding="utf-8"))["stars"]
    return np.array([r[0] for r in rows]), np.array([r[1] for r in rows])


def cell_of(alt: float, az: float) -> int | None:
    """The cell index of a direction (degrees); None below 15°."""
    if alt < BANDS[0]:
        return None
    band = next((i for i in range(len(BANDS) - 1) if alt < BANDS[i + 1]), len(BANDS) - 2)
    sector = int(((az + 22.5) % 360.0) // 45.0)
    return SECTORS * band + sector


def cell_centre(index: int) -> tuple[float, float]:
    """(alt, az) of a cell's centre in degrees."""
    band, sector = divmod(index, SECTORS)
    return (BANDS[band] + BANDS[band + 1]) / 2.0, sector * 45.0


def _separation(alt1: float, az1: float, alt2: float, az2: float) -> float:
    a1, a2 = math.radians(alt1), math.radians(alt2)
    c = math.sin(a1) * math.sin(a2) + math.cos(a1) * math.cos(a2) * math.cos(
        math.radians(az1 - az2)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _moon_cells(at: datetime, lat: float, lon: float) -> set[int]:
    """Cells whose centre is within 25° of a moon at least 25 % lit."""
    if moon_illumination(at) < MOON_LIT:
        return set()
    ra, dec = moon_radec(at)
    m_alt, m_az = alt_az(np.array([ra]), np.array([dec]), sidereal_deg(at, lon), lat)
    malt, maz = float(m_alt[0]), float(m_az[0])
    if malt < -MOON_DEG:
        return set()
    return {i for i in range(CELLS) if _separation(*cell_centre(i), malt, maz) <= MOON_DEG}


def cloud_map(
    solution: Solution,
    shape: tuple[int, int],
    sky: Mask,
    xs: npt.NDArray[np.intp],
    ys: npt.NDArray[np.intp],
    captured_at: datetime,
    exposure_us: int,
    lat: float,
    lon: float,
) -> list[float | None]:
    """The 24 cell values for a frame of `shape` (h, w) with detected stars at (xs, ys)."""
    h, w = shape
    scale = w / solution.width if solution.width else 1.0
    mid = captured_at + timedelta(microseconds=exposure_us / 2)
    ra, dec = _stars()
    alt, az = alt_az(ra, dec, sidereal_deg(mid, lon), lat)
    up = alt >= BANDS[0]
    alt, az = alt[up], az[up]
    px, py = solution.project(alt, az)
    px, py = px * scale, py * scale
    # One degree in pixels where the star is: dr/dalt of r = a1·t + a3·t³, t = (90 - alt)/90.
    t = (90.0 - alt) / 90.0
    tol = np.maximum(2.0, (solution.a1 + 3.0 * solution.a3 * t * t) / 90.0 * TOLERANCE_DEG * scale)
    detected = np.zeros((h, w), dtype=np.bool_)
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    detected[ys[ok], xs[ok]] = True
    expected = [0] * CELLS
    found = [0] * CELLS
    for i in range(alt.size):
        x, y = float(px[i]), float(py[i])
        if not (0 <= x < w and 0 <= y < h) or not sky[int(y), int(x)]:
            continue
        cell = cell_of(float(alt[i]), float(az[i]))
        if cell is None:
            continue
        expected[cell] += 1
        r = float(tol[i])
        x0, x1 = max(0, int(x - r)), min(w, int(x + r) + 1)
        y0, y1 = max(0, int(y - r)), min(h, int(y + r) + 1)
        if detected[y0:y1, x0:x1].any():
            found[cell] += 1
    moon = _moon_cells(mid, lat, lon)
    return [
        None if expected[i] < MIN_EXPECTED or i in moon else round(1.0 - found[i] / expected[i], 3)
        for i in range(CELLS)
    ]
