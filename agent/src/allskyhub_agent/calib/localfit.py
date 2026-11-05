"""A bright meteor's track in the sky from the stars around it (SPEC §4.8 Local fit for
bright meteors, fireball package B).

The orientation (SPEC §4.8) is fitted to the whole sky, so a few tenths of a degree off in
places. For a bright or long meteor the stars of its own frame near the track pin down
the directions there: the orientation, corrected by a small rotation of the sky - a shift
in altitude and azimuth and a turn about the track's midpoint, each at most 1° - fitted
to the stars within 15° of the track. Accepted only with enough stars and a small error,
else the orientation alone stands. It runs in the detection thread, never in capture,
and gives up after a CPU budget.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path

import numpy as np
import numpy.typing as npt

from allskyhub_agent.calib.solve import Gray, Solution, alt_az, sidereal_deg, star_map

F = npt.NDArray[np.float64]

STARS = Path(__file__).with_name("stars_mag4.json")  # Hipparcos to V 4.0
MAX_MAG = 4.0
NEAR_DEG = 15.0  # stars this close to the track
MAX_CORRECTION_DEG = 1.0  # each of the three angles
MIN_STARS = 8
MAX_RMS_DEG = 0.25
BUDGET_S = 2.0  # CPU seconds (Pi 4)
MIN_ALT = 3.0
SEARCH_DEG = (1.6, 0.8, 0.45)  # star search radius per round, in degrees of sky
DOMINANCE = 1.35  # the star's peak against anything else in its window


@dataclass(frozen=True)
class LocalFit:
    """The orientation turned by `rotation` (true sky -> the orientation's sky)."""

    sol: Solution
    rotation: F  # 3 x 3
    stars: int
    rms_deg: float
    angles_deg: tuple[float, float, float]  # altitude shift, azimuth shift, turn

    def position(self, x: float, y: float) -> tuple[float, float]:
        """(azimuth, altitude) in degrees of a pixel, corrected."""
        alt, az = self.sol.unproject(np.array([x]), np.array([y]))
        v = self.rotation.T @ unit(float(alt[0]), float(az[0]))
        return to_az_alt(v)


class Budget:
    """CPU time of this thread, so a busy Pi does not count against the fit."""

    def __init__(self, seconds: float) -> None:
        self._end = time.thread_time() + seconds

    def over(self) -> bool:
        return time.thread_time() > self._end


def unit(alt: float, az: float) -> F:
    """East, north, up of a direction in degrees."""
    a, z = math.radians(alt), math.radians(az)
    return np.array([math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)])


def units(alt: F, az: F) -> F:
    a, z = np.radians(alt).ravel(), np.radians(az).ravel()
    out = np.empty((a.size, 3))
    out[:, 0], out[:, 1], out[:, 2] = np.cos(a) * np.sin(z), np.cos(a) * np.cos(z), np.sin(a)
    return out


def _clip(v: F, lo: float, hi: float) -> F:
    return np.minimum(np.maximum(v, lo), hi)


def to_az_alt(v: F) -> tuple[float, float]:
    v = v / float(np.linalg.norm(v))
    alt = math.degrees(math.asin(max(-1.0, min(1.0, float(v[2])))))
    az = math.degrees(math.atan2(float(v[0]), float(v[1]))) % 360.0
    return az, alt


def _axis_rotation(axis: F, deg: float) -> F:
    k = axis / float(np.linalg.norm(axis))
    a = math.radians(deg)
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + math.sin(a) * kx + (1.0 - math.cos(a)) * (kx @ kx)


def correction(mid: F, d_alt: float, d_az: float, turn: float) -> F:
    """True sky -> the orientation's sky: raise the midpoint by `d_alt`, move it `d_az`
    in azimuth (clockwise from north, seen from below), turn about it by `turn`."""
    up = np.array([0.0, 0.0, 1.0])
    side = np.cross(mid, up)  # horizontal, square to the midpoint's vertical
    if float(np.linalg.norm(side)) < 1e-9:  # the midpoint at the zenith
        side = np.array([1.0, 0.0, 0.0])
    r_alt = _axis_rotation(side, d_alt)
    r_az = _axis_rotation(-up, d_az)
    r_turn = _axis_rotation(mid, turn)
    return r_turn @ r_az @ r_alt


@cache
def _stars() -> tuple[F, F, F]:
    rows: list[list[float]] = json.loads(STARS.read_text(encoding="utf-8"))["stars"]
    return (np.array([r[0] for r in rows]), np.array([r[1] for r in rows]),
            np.array([r[2] for r in rows]))  # fmt: skip


def near_stars(track: F, utc: datetime, lat: float, lon: float) -> F:
    """Unit vectors of the catalogue stars (V <= 4) above the horizon within 15° of the
    track (n x 3 unit vectors along it)."""
    ra, dec, mag = _stars()
    keep = mag <= MAX_MAG
    alt, az = alt_az(ra[keep], dec[keep], sidereal_deg(utc, lon), lat)
    up = alt >= MIN_ALT
    v = units(alt[up], az[up])
    if len(v) == 0:
        return v
    # The track is a great-circle arc, sampled densely enough for the distance.
    closest = np.max(v @ track.T, axis=1)
    return v[closest >= math.cos(math.radians(NEAR_DEG))]


def _project(sol: Solution, v: F) -> tuple[F, F]:
    alt = np.degrees(np.arcsin(_clip(v[:, 2], -1.0, 1.0)))
    az = np.degrees(np.arctan2(v[:, 0], v[:, 1])) % 360.0
    return sol.project(alt, az)


def _px_per_deg(sol: Solution, v: F) -> F:
    """The plate scale at each direction (radial, like solve.residuals)."""
    t = (90.0 - np.degrees(np.arcsin(_clip(v[:, 2], -1.0, 1.0)))) / 90.0
    return np.maximum((sol.a1 + 3 * sol.a3 * t**2) / 90.0, 1e-6)


def _match(
    stars: npt.NDArray[np.float32], usable: npt.NDArray[np.bool_], thr: float,
    x: F, y: F, radius: F,
) -> tuple[npt.NDArray[np.int64], F]:  # fmt: skip
    """For each predicted place the clear brightest point of the star map within its
    radius: the indices that have one and their (x, y)."""
    h, w = stars.shape
    idx: list[int] = []
    pts: list[tuple[float, float]] = []
    for i, (px, py, rad) in enumerate(zip(x.tolist(), y.tolist(), radius.tolist(), strict=True)):
        if not (math.isfinite(px) and math.isfinite(py)):
            continue
        r = max(2, int(rad))
        x0, y0, x1, y1 = int(px) - r, int(py) - r, int(px) + r + 1, int(py) + r + 1
        if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
            continue
        win = stars[y0:y1, x0:x1].astype(np.float64)
        win[~usable[y0:y1, x0:x1]] = -1e9
        jy, jx = divmod(int(np.argmax(win)), win.shape[1])
        best = float(win[jy, jx])
        if best < thr:
            continue
        yy, xx = np.ogrid[: win.shape[0], : win.shape[1]]
        win[(yy - jy) ** 2 + (xx - jx) ** 2 < 36] = -1e9
        if best < DOMINANCE * max(float(win.max()), 1.0):
            continue
        idx.append(i)
        pts.append((float(x0 + jx), float(y0 + jy)))
    # One point can't be two stars.
    seen: dict[tuple[float, float], int] = {}
    for p in pts:
        seen[p] = seen.get(p, 0) + 1
    keep = [k for k, p in enumerate(pts) if seen[p] == 1]
    return (np.array([idx[k] for k in keep], dtype=np.int64),
            np.array([pts[k] for k in keep], dtype=np.float64).reshape(-1, 2))  # fmt: skip


def _fit_angles(
    sol: Solution, mid: F, v: F, xy: F, start: tuple[float, float, float], budget: Budget
) -> tuple[float, float, float] | None:
    """Gauss-Newton with soft-L1 weights (3 px) on the three angles, kept within 1°."""
    q = np.array(start, dtype=np.float64)

    def resid(a: F) -> F:
        x, y = _project(sol, v @ correction(mid, *a.tolist()).T)
        return np.concatenate([x - xy[:, 0], y - xy[:, 1]])

    for _ in range(12):
        if budget.over():
            return None
        r = resid(q)
        wgt = 1.0 / np.sqrt(1.0 + (r / 3.0) ** 2)
        jac = np.empty((r.size, 3))
        for j in range(3):
            step = np.zeros(3)
            step[j] = 1e-3
            jac[:, j] = (resid(q + step) - r) / 1e-3
        jw = jac * wgt[:, None]
        try:
            delta = np.linalg.solve(jw.T @ jw + 1e-6 * np.eye(3), -(jw.T @ (r * wgt)))
        except np.linalg.LinAlgError:
            return None
        q = _clip(q + delta, -MAX_CORRECTION_DEG, MAX_CORRECTION_DEG)
        if float(np.abs(delta).max()) < 1e-4:
            break
    return float(q[0]), float(q[1]), float(q[2])


def _rms_deg(sol: Solution, rotation: F, v: F, xy: F) -> float:
    """RMS angle between each corrected star and where it was found."""
    alt, az = sol.unproject(xy[:, 0], xy[:, 1])
    found = units(alt, az)
    cos = _clip(np.sum((v @ rotation.T) * found, axis=1), -1.0, 1.0)
    return float(np.sqrt(np.mean(np.degrees(np.arccos(cos)) ** 2)))


def fit(
    gray: Gray,
    sol: Solution,
    track_px: list[tuple[float, float]],
    utc: datetime,
    lat: float,
    lon: float,
    streak_px: tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]] | None = None,
    budget_s: float = BUDGET_S,
) -> LocalFit | None:
    """The local fit around a meteor's track (its centre line in pixels, start to end),
    or None: too few stars, too large an error, or out of time."""
    budget = Budget(budget_s)
    h, w = gray.shape
    if (sol.width and (w, h) != (sol.width, sol.height)) or len(track_px) < 2:
        return None
    tx, ty = np.array([p[0] for p in track_px]), np.array([p[1] for p in track_px])
    t_alt, t_az = sol.unproject(tx, ty)
    track = units(t_alt, t_az)
    mid = track[len(track) // 2]
    v = near_stars(track, utc, lat, lon)
    if len(v) < MIN_STARS or budget.over():
        return None
    # Only the part of the frame the stars can be in, with the search radius around it.
    x, y = _project(sol, v)
    scale = _px_per_deg(sol, v)
    pad = int(float(np.max(scale)) * (SEARCH_DEG[0] + MAX_CORRECTION_DEG)) + 8
    ok = np.isfinite(x) & np.isfinite(y)
    if not ok.any():
        return None
    x0 = max(0, int(np.min(x[ok])) - pad)
    y0 = max(0, int(np.min(y[ok])) - pad)
    x1 = min(w, int(np.max(x[ok])) + pad + 1)
    y1 = min(h, int(np.max(y[ok])) + pad + 1)
    crop = np.ascontiguousarray(gray[y0:y1, x0:x1])
    stars = star_map(crop)
    ch, cw = stars.shape[0], stars.shape[1]
    usable = np.ones((ch, cw), dtype=np.bool_)
    if streak_px is not None:  # the meteor itself is no star
        sx, sy = streak_px[0] - x0, streak_px[1] - y0
        inside = (sx >= 0) & (sx < cw) & (sy >= 0) & (sy < ch)
        for d in range(-6, 7):
            for e in range(-6, 7):
                xs, ys = sx[inside] + d, sy[inside] + e
                good = (xs >= 0) & (xs < cw) & (ys >= 0) & (ys < ch)
                usable[ys[good], xs[good]] = False
    sample = stars[usable][::7]
    if sample.size == 0 or budget.over():
        return None
    thr = max(3.0, 12.0 * 1.4826 * float(np.median(np.abs(sample - np.median(sample)))))
    angles = (0.0, 0.0, 0.0)
    idx = np.empty(0, dtype=np.int64)
    xy = np.empty((0, 2))
    for radius_deg in SEARCH_DEG:
        rot = correction(mid, *angles)
        px, py = _project(sol, v @ rot.T)
        idx, xy = _match(stars, usable, thr, px - x0, py - y0, scale * radius_deg)
        if len(idx) < MIN_STARS or budget.over():
            return None
        xy = xy + np.array([x0, y0], dtype=np.float64)
        got = _fit_angles(sol, mid, v[idx], xy, angles, budget)
        if got is None:
            return None
        angles = got
    rot = correction(mid, *angles)
    rms = _rms_deg(sol, rot, v[idx], xy)
    if len(idx) < MIN_STARS or not math.isfinite(rms) or rms > MAX_RMS_DEG:
        return None
    return LocalFit(sol=sol, rotation=rot, stars=len(idx), rms_deg=round(rms, 3),
                    angles_deg=(round(angles[0], 3), round(angles[1], 3),
                                round(angles[2], 3)))  # fmt: skip
