"""Sky brightness per sky cell (SPEC §6.3 `brightness_map`, roadmap L37).

48 cells: 16 azimuth sectors of 22.5° (sector k from k·22.5° to (k+1)·22.5°, so each lies
inside one `cloud_map` sector) x the 3 altitude bands of `cloud_map`, index = 16·band +
sector. On the frame scaled to 960 px width and on linear values, a cell's brightness is
the median of its sky pixels without clipped ones and without those within 3 px of a
detected star, corrected for throughput and pixel solid angle: the twilight flat holds
both, else the lens model's relative pixel solid angle Ω_px = sinθ / (r · dr/dθ) is
divided out and the vignetting stays in. Each cell is anchored to the frame's `sqm_mag`:
value = sqm_mag - 2.5·log10(L_cell / L_ref), L_ref the linear mean of the unflattened
frame over the sky mask, which is what the SQM zero point was fitted on.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.detect.cloudmap import BANDS, MOON_DEG, MOON_LIT
from allskyhub_sky.astro import moon_illumination, moon_radec
from allskyhub_solve.solve import Solution, alt_az, sidereal_deg, unproject

Mask = npt.NDArray[np.bool_]
Image = npt.NDArray[np.uint8]
Points = npt.NDArray[np.intp]
Flat = Literal["twilight", "geometry"]

SECTORS = 16
SECTOR_DEG = 360.0 / SECTORS
CELLS = SECTORS * (len(BANDS) - 1)
WIDTH = 960
MIN_PIXELS = 200
STAR_PX = 3
CLIP_LEVEL = 250  # a pixel with any channel at or above this is clipped
LOWEST, HIGHEST = 14.0, 23.0


def cell_centre(index: int) -> tuple[float, float]:
    """(alt, az) of a cell's centre in degrees."""
    band, sector = divmod(index, SECTORS)
    return (BANDS[band] + BANDS[band + 1]) / 2.0, sector * SECTOR_DEG + SECTOR_DEG / 2.0


def cloud_cell(index: int) -> int:
    """The `cloud_map` cell this cell lies in."""
    band, sector = divmod(index, SECTORS)
    return 8 * band + ((sector + 1) // 2) % 8


def _separation(alt1: float, az1: float, alt2: float, az2: float) -> float:
    a1, a2 = math.radians(alt1), math.radians(alt2)
    c = math.sin(a1) * math.sin(a2) + math.cos(a1) * math.cos(a2) * math.cos(
        math.radians(az1 - az2)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def moon_cells(at: datetime, lat: float, lon: float) -> set[int]:
    """Cells whose centre is within 25° of a moon at least 25 % lit."""
    if moon_illumination(at) < MOON_LIT:
        return set()
    ra, dec = moon_radec(at)
    m_alt, m_az = alt_az(np.array([ra]), np.array([dec]), sidereal_deg(at, lon), lat)
    malt, maz = float(m_alt[0]), float(m_az[0])
    if malt < -MOON_DEG:
        return set()
    return {i for i in range(CELLS) if _separation(*cell_centre(i), malt, maz) <= MOON_DEG}


def solid_angle(
    sol: Solution, px: npt.NDArray[np.float64], py: npt.NDArray[np.float64]
) -> npt.NDArray[np.float32]:
    """The pixel solid angle of r = a1·t + a3·t³ (t = θ/90°) relative to the lens centre:
    Ω_px = sinθ / (r · dr/dθ), at the centre (π/2)² / a1². Pixels of the solved frame;
    the zenith angle is the lens's own, before the tilt."""
    level = [sol.cx, sol.cy, sol.a1, sol.a3, sol.rot, 0.0, 0.0]
    alt, _ = unproject(px, py, level, sol.flip)
    t = (90.0 - alt) / 90.0
    theta = t * (math.pi / 2.0)
    r = sol.a1 * t + sol.a3 * t**3
    drdtheta = (sol.a1 + 3.0 * sol.a3 * t * t) / (math.pi / 2.0)
    centre = (math.pi / 2.0) ** 2 / sol.a1**2
    small = r < 1e-6
    omega = np.sin(theta) / np.maximum(r * drdtheta, 1e-12)
    return (np.where(small, centre, omega) / centre).astype(np.float32)


@dataclass
class _Geometry:
    key: tuple[int, int, Solution]
    size: tuple[int, int]  # (w, h) of the scaled frame
    cell: npt.NDArray[np.int16]  # (h, w) cell index, -1 below 15° or outside
    # (h, w) relative pixel solid angle, only worked out once a frame needs it (no flat).
    omega: npt.NDArray[np.float32] | None = None

    def solid_angle(self) -> npt.NDArray[np.float32]:
        if self.omega is None:
            sw, sh = self.size
            px, py = _centres(sw, sh, self.key[1], self.key[2])
            self.omega = solid_angle(self.key[2], px, py).reshape(sh, sw)
        return self.omega


def _centres(
    sw: int, sh: int, w: int, sol: Solution
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Pixel centres of the scaled frame in the coordinates of the solved frame."""
    ys, xs = np.mgrid[0:sh, 0:sw]
    scale = (sol.width or w) / sw
    return (xs.ravel().astype(np.float64) + 0.5) * scale, (
        ys.ravel().astype(np.float64) + 0.5
    ) * scale


def _geometry(h: int, w: int, sol: Solution) -> _Geometry:
    sw = min(WIDTH, w)
    sh = max(1, round(h * sw / w))
    px, py = _centres(sw, sh, w, sol)
    alt, az = sol.unproject(px, py)
    band = np.searchsorted(np.array(BANDS), alt, side="right") - 1
    band = np.minimum(band, len(BANDS) - 2)
    sector = (np.floor((az % 360.0) / SECTOR_DEG).astype(np.int64)) % SECTORS
    cell = np.where(alt >= BANDS[0], SECTORS * band + sector, -1).astype(np.int16)
    return _Geometry((h, w, sol), (sw, sh), cell.reshape(sh, sw))


def _linear_lut(srgb: bool) -> npt.NDArray[np.float32]:
    x = np.arange(256, dtype=np.float64) / 255.0
    if srgb:
        x = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    return x.astype(np.float32)


def _gray(image: Image) -> Image:
    if image.ndim == 3:
        return np.asarray(cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY), dtype=np.uint8)
    return image


def cell_medians(
    cell: npt.NDArray[np.int16], values: npt.NDArray[np.float32], keep: Mask
) -> list[float | None]:
    """The median of `values` per cell over the `keep` pixels, None with fewer than 200."""
    idx = cell[keep].astype(np.int64)
    vals = values[keep]
    order = np.lexsort((vals, idx))
    idx, vals = idx[order], vals[order]
    counts = np.bincount(idx, minlength=CELLS)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    out: list[float | None] = []
    for i in range(CELLS):
        n = int(counts[i])
        if n < MIN_PIXELS:
            out.append(None)
            continue
        s = int(starts[i])
        lo, hi = s + (n - 1) // 2, s + n // 2
        out.append((float(vals[lo]) + float(vals[hi])) / 2.0)
    return out


class BrightnessMeter:
    """SPEC §6.3 `brightness_map` of a frame; the geometry is kept per frame size and lens."""

    def __init__(self, srgb: bool) -> None:
        self._lut = _linear_lut(srgb)
        self._geo: _Geometry | None = None
        self._kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * STAR_PX + 1, 2 * STAR_PX + 1)
        )

    def measure(
        self,
        sol: Solution,
        image: Image,
        raw: Image,
        flat: Flat,
        sky: Mask,
        xs: Points,
        ys: Points,
        sqm_mag: float,
        at: datetime,
        lat: float,
        lon: float,
    ) -> list[float | None] | None:
        """`image` divided by the twilight flat (used with `flat` "twilight"), `raw` the
        frame without any flat; `sky` the sky mask and (xs, ys) the stars, both full size."""
        h, w = raw.shape[:2]
        if self._geo is None or self._geo.key != (h, w, sol):
            self._geo = _geometry(h, w, sol)
        geo = self._geo
        lut = self._lut
        ref = float(lut[_gray(raw)[sky]].mean()) if sky.any() else 0.0
        if ref <= 0.0:
            return None
        src = image if flat == "twilight" else raw
        sw, sh = geo.size
        small = (
            src if (w, h) == geo.size else cv2.resize(src, geo.size, interpolation=cv2.INTER_AREA)
        )
        small = np.asarray(small, dtype=np.uint8)
        clipped = (small.max(axis=2) if small.ndim == 3 else small) >= CLIP_LEVEL
        lin = lut[_gray(small)]
        if flat == "geometry":
            lin = lin / geo.solid_angle()
        stars = np.zeros((sh, sw), dtype=np.uint8)
        sx = np.minimum((xs.astype(np.float64) * sw / w).astype(np.intp), sw - 1)
        sy = np.minimum((ys.astype(np.float64) * sh / h).astype(np.intp), sh - 1)
        stars[sy, sx] = 1
        near = np.asarray(cv2.dilate(stars, self._kernel), dtype=np.uint8) > 0
        sky_small = (
            sky
            if (w, h) == geo.size
            else np.asarray(
                cv2.resize(sky.astype(np.uint8) * 255, geo.size, interpolation=cv2.INTER_AREA)
            )
            > 127
        )
        keep = (geo.cell >= 0) & sky_small & ~clipped & ~near
        medians = cell_medians(geo.cell, lin.astype(np.float32), keep)
        moon = moon_cells(at, lat, lon)
        out: list[float | None] = []
        for i, m in enumerate(medians):
            if m is None or m <= 0.0 or i in moon:
                out.append(None)
                continue
            mag = sqm_mag - 2.5 * math.log10(m / ref)
            out.append(round(min(HIGHEST, max(LOWEST, mag)), 2))
        return out
