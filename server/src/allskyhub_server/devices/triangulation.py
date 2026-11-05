"""Where a meteor flew, from two cameras or more (roadmap C4, SPEC §6.4 track ends).

Each camera sees the track as two directions (azimuth, altitude of both ends); together with
the camera's position they span a plane. Two cameras' planes meet in the meteor's line of
flight; the ends each camera saw are the points on that line closest to its end rays.
Heights are above the WGS84 ellipsoid. Without the direction of flight (``track_ordered``
false) the result has no "begin" and "end", just two ends, the higher listed first.

``solve`` takes every camera that saw the meteor at once: the line closest to all planes
(least squares, a plane weighted by the track's length on the sky over the camera's error),
and each end as the weighted mean of where the cameras' end rays meet it. That assumes all
of them saw the whole glow, as one long exposure does; a track cut by a horizon pulls its
end a little. How far to trust it comes from solving again with the directions shaken by
each camera's plate-solve error (Monte Carlo; Benjamin, 2026-11-05).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

A = 6378.137  # WGS84, km
F = 1 / 298.257223563
E2 = F * (2 - F)
# Planes meeting at a shallower angle give a line that is too uncertain to report.
MIN_CONVERGENCE_DEG = 10.0
# Real meteors burn up between roughly 20 and 150 km.
MIN_HEIGHT_KM, MAX_HEIGHT_KM = 15.0, 180.0
# Part of the shorter of the two seen stretches that both cameras must have in common.
MIN_OVERLAP = 0.3
# The error of a camera's directions (one sigma, degrees) when it reports none, about a plate
# solve's usual RMS (SPEC §4.8 accepts up to 0.6°); never less than a pixel or so.
DEFAULT_SIGMA_DEG = 0.3
MIN_SIGMA_DEG = 0.1
# Solutions with shaken directions; fewer than half of them valid: no error to give.
RUNS = 200

Vec = tuple[float, float, float]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Vec) -> Vec:
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def ecef(lat: float, lon: float, height_km: float = 0.0) -> Vec:
    la, lo = math.radians(lat), math.radians(lon)
    n = A / math.sqrt(1 - E2 * math.sin(la) ** 2)
    return ((n + height_km) * math.cos(la) * math.cos(lo),
            (n + height_km) * math.cos(la) * math.sin(lo),
            (n * (1 - E2) + height_km) * math.sin(la))  # fmt: skip


def geodetic(p: Vec) -> tuple[float, float, float]:
    """ECEF → latitude, longitude (degrees), height (km), Bowring's iteration."""
    x, y, z = p
    lon = math.atan2(y, x)
    r = math.hypot(x, y)
    lat = math.atan2(z, r * (1 - E2))
    for _ in range(6):
        n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
        h = r / math.cos(lat) - n
        lat = math.atan2(z, r * (1 - E2 * n / (n + h)))
    n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
    h = r / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


def direction(lat: float, lon: float, azimuth: float, altitude: float) -> Vec:
    """Unit vector in ECEF for a direction seen from (lat, lon): azimuth from north
    towards east, altitude above the horizon."""
    la, lo = math.radians(lat), math.radians(lon)
    az, alt = math.radians(azimuth), math.radians(altitude)
    e = math.cos(alt) * math.sin(az)
    n = math.cos(alt) * math.cos(az)
    u = math.sin(alt)
    east: Vec = (-math.sin(lo), math.cos(lo), 0.0)
    north: Vec = (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la))
    up: Vec = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    return _unit(_add(_add(_mul(east, e), _mul(north, n)), _mul(up, u)))


@dataclass(frozen=True)
class Sighting:
    """One camera's view of a meteor: its place and the directions of both track ends."""

    lat: float
    lon: float
    height_km: float
    end1: tuple[float, float]  # azimuth, altitude
    end2: tuple[float, float]
    sigma_deg: float = DEFAULT_SIGMA_DEG


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float
    height_km: float


@dataclass(frozen=True)
class Trajectory:
    points: tuple[Point, Point]  # higher end first
    length_km: float
    convergence_deg: float


def _closest_on_line(line_p: Vec, line_d: Vec, origin: Vec, ray: Vec) -> Vec:
    """The point on the line (line_p + t·line_d) closest to the ray from ``origin``."""
    w0 = _sub(line_p, origin)
    a, b, c = _dot(line_d, line_d), _dot(line_d, ray), _dot(ray, ray)
    d, e = _dot(line_d, w0), _dot(ray, w0)
    denom = a * c - b * b
    t = (b * e - c * d) / denom if abs(denom) > 1e-12 else 0.0
    return _add(line_p, _mul(line_d, t))


def triangulate(s1: Sighting, s2: Sighting) -> Trajectory | None:
    """None when the planes meet at less than ``MIN_CONVERGENCE_DEG`` or the result is not
    at meteor heights (then the two sightings were not the same meteor)."""
    o1, o2 = ecef(s1.lat, s1.lon, s1.height_km), ecef(s2.lat, s2.lon, s2.height_km)
    d1 = [direction(s1.lat, s1.lon, *e) for e in (s1.end1, s1.end2)]
    d2 = [direction(s2.lat, s2.lon, *e) for e in (s2.end1, s2.end2)]
    n1, n2 = _cross(d1[0], d1[1]), _cross(d2[0], d2[1])
    if _norm(n1) < 1e-9 or _norm(n2) < 1e-9:
        return None
    n1, n2 = _unit(n1), _unit(n2)
    angle = math.degrees(math.acos(min(1.0, abs(_dot(n1, n2)))))
    if angle < MIN_CONVERGENCE_DEG:
        return None
    line_d = _unit(_cross(n1, n2))
    # A point on both planes: n1·p = n1·o1, n2·p = n2·o2, closest to the origin.
    h1, h2 = _dot(n1, o1), _dot(n2, o2)
    k = _dot(n1, n2)
    det = 1 - k * k
    c1, c2 = (h1 - h2 * k) / det, (h2 - h1 * k) / det
    line_p = _add(_mul(n1, c1), _mul(n2, c2))
    # Each camera's ends on the line; both must cover a common stretch, else these were
    # two different meteors that happen to give crossing planes.
    ends1 = [_closest_on_line(line_p, line_d, o1, d) for d in d1]
    ends2 = [_closest_on_line(line_p, line_d, o2, d) for d in d2]
    s1_ = sorted(_dot(_sub(p, line_p), line_d) for p in ends1)
    s2_ = sorted(_dot(_sub(p, line_p), line_d) for p in ends2)
    overlap = min(s1_[1], s2_[1]) - max(s1_[0], s2_[0])
    shorter = min(s1_[1] - s1_[0], s2_[1] - s2_[0])
    if shorter <= 0 or overlap < MIN_OVERLAP * shorter:
        return None
    ends = ends1
    points = [geodetic(p) for p in ends]
    if not all(MIN_HEIGHT_KM <= h <= MAX_HEIGHT_KM for _, _, h in points):
        return None
    pts = sorted((Point(lat, lon, h) for lat, lon, h in points), key=lambda p: -p.height_km)
    return Trajectory(
        (pts[0], pts[1]), length_km=_norm(_sub(ends[0], ends[1])), convergence_deg=angle
    )


@dataclass(frozen=True)
class Solution:
    """All cameras' trajectory and how far its ends may be off (one sigma, km)."""

    trajectory: Trajectory
    cameras: int
    high_err_km: float | None
    low_err_km: float | None


def _fit(
    origins: list[Vec], rays: list[tuple[Vec, Vec]], sigmas: list[float]
) -> tuple[Vec, Vec] | None:
    """The line closest to all cameras' planes and its two ends, ECEF."""
    normals: list[Vec] = []
    weights: list[float] = []
    for (d1, d2), sigma in zip(rays, sigmas, strict=True):
        n = _cross(d1, d2)
        if _norm(n) < 1e-9:
            return None
        # |d1 x d2| is the sine of the track's length: a short track tilts its plane easily.
        weights.append((_norm(n) / sigma) ** 2)
        normals.append(_unit(n))
    nm = np.array(normals, dtype=np.float64)
    w = np.array(weights, dtype=np.float64)
    m = (nm * w[:, None]).T @ nm
    _, vectors = np.linalg.eigh(m)
    line_d: Vec = (float(vectors[0, 0]), float(vectors[1, 0]), float(vectors[2, 0]))
    # The point on the line nearest the cameras' centre fixes it along its direction.
    centre: Vec = (0.0, 0.0, 0.0)
    for o in origins:
        centre = _add(centre, o)
    centre = _mul(centre, 1 / len(origins))
    rows = np.array(
        [_mul(n, math.sqrt(wi)) for n, wi in zip(normals, weights, strict=True)] + [line_d],
        dtype=np.float64,
    )
    rhs = np.array(
        [math.sqrt(wi) * _dot(n, o) for n, o, wi in zip(normals, origins, weights, strict=True)]
        + [_dot(line_d, centre)],
        dtype=np.float64,
    )
    found = np.linalg.lstsq(rows, rhs, rcond=None)[0]
    line_p: Vec = (float(found[0]), float(found[1]), float(found[2]))
    # Each camera's ends along the line, the lower one first; a ray that meets the line at
    # a flat angle, or a camera with a rough solve, places its end loosely.
    ends: list[tuple[float, float]] = [(0.0, 0.0), (0.0, 0.0)]
    for o, pair, sigma in zip(origins, rays, sigmas, strict=True):
        placed = sorted(
            (_dot(_sub(_closest_on_line(line_p, line_d, o, d), line_p), line_d),
             (1 - _dot(d, line_d) ** 2) / sigma**2)
            for d in pair
        )  # fmt: skip
        for i, (t, w_end) in enumerate(placed):
            ends[i] = (ends[i][0] + t * w_end, ends[i][1] + w_end)
    if ends[0][1] <= 0 or ends[1][1] <= 0:
        return None
    t_lo, t_hi = ends[0][0] / ends[0][1], ends[1][0] / ends[1][1]
    return _add(line_p, _mul(line_d, t_lo)), _add(line_p, _mul(line_d, t_hi))


def _rays(s: Sighting, rng: random.Random | None = None) -> tuple[Vec, Vec]:
    out: list[Vec] = []
    for az, alt in (s.end1, s.end2):
        if rng is not None:
            sigma = max(s.sigma_deg, MIN_SIGMA_DEG)
            az += rng.gauss(0, sigma) / max(math.cos(math.radians(alt)), 0.05)
            alt += rng.gauss(0, sigma)
        out.append(direction(s.lat, s.lon, az, alt))
    return out[0], out[1]


def _ends(ends: tuple[Vec, Vec]) -> tuple[Point, Point] | None:
    """Higher end first; None off meteor heights."""
    points = [Point(*geodetic(p)) for p in ends]
    if not all(MIN_HEIGHT_KM <= p.height_km <= MAX_HEIGHT_KM for p in points):
        return None
    high, low = sorted(points, key=lambda p: -p.height_km)
    return high, low


def solve(sightings: list[Sighting], seed: str = "") -> Solution | None:
    """One trajectory from all cameras (they passed ``triangulate`` in pairs); ``seed``
    makes the shaken solutions repeatable."""
    if len(sightings) < 2:
        return None
    origins = [ecef(s.lat, s.lon, s.height_km) for s in sightings]
    sigmas = [max(s.sigma_deg, MIN_SIGMA_DEG) for s in sightings]
    rays = [_rays(s) for s in sightings]
    fitted = _fit(origins, rays, sigmas)
    ends = _ends(fitted) if fitted is not None else None
    if fitted is None or ends is None:
        return None
    normals = [_unit(_cross(*r)) for r in rays]
    convergence = max(
        math.degrees(math.acos(min(1.0, abs(_dot(a, b)))))
        for i, a in enumerate(normals)
        for b in normals[i + 1 :]
    )
    high, low = ends
    nominal = (ecef(high.lat, high.lon, high.height_km), ecef(low.lat, low.lon, low.height_km))
    rng = random.Random(seed)
    spread: list[tuple[float, float]] = []
    for _ in range(RUNS):
        shaken = _fit(origins, [_rays(s, rng) for s in sightings], sigmas)
        got = _ends(shaken) if shaken is not None else None
        if got is None:
            continue
        a, b = (ecef(p.lat, p.lon, p.height_km) for p in got)
        spread.append((_norm(_sub(a, nominal[0])), _norm(_sub(b, nominal[1]))))
    errors: tuple[float | None, float | None] = (None, None)
    if len(spread) >= RUNS // 2:
        errors = (
            math.sqrt(sum(h * h for h, _ in spread) / len(spread)),
            math.sqrt(sum(lo * lo for _, lo in spread) / len(spread)),
        )
    return Solution(
        Trajectory((high, low), length_km=_norm(_sub(*fitted)), convergence_deg=convergence),
        cameras=len(sightings),
        high_err_km=errors[0],
        low_err_km=errors[1],
    )


def look(lat: float, lon: float, height_km: float, target: Vec) -> tuple[float, float]:
    """Azimuth and altitude of an ECEF point seen from a place (for tests and checks)."""
    o = ecef(lat, lon, height_km)
    d = _unit(_sub(target, o))
    la, lo = math.radians(lat), math.radians(lon)
    east: Vec = (-math.sin(lo), math.cos(lo), 0.0)
    north: Vec = (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la))
    up: Vec = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    alt = math.degrees(math.asin(_dot(d, up)))
    az = math.degrees(math.atan2(_dot(d, east), _dot(d, north))) % 360
    return az, alt
