"""Where a meteor flew, from two cameras (roadmap C4, SPEC §6.4 track ends).

Each camera sees the track as two directions (azimuth, altitude of both ends); together with
the camera's position they span a plane. Two cameras' planes meet in the meteor's line of
flight; the ends each camera saw are the points on that line closest to its end rays.
Heights are above the WGS84 ellipsoid. Without the direction of flight (``track_ordered``
false) the result has no "begin" and "end", just two ends, the higher listed first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

A = 6378.137  # WGS84, km
F = 1 / 298.257223563
E2 = F * (2 - F)
# Planes meeting at a shallower angle give a line that is too uncertain to report.
MIN_CONVERGENCE_DEG = 10.0
# Real meteors burn up between roughly 20 and 150 km.
MIN_HEIGHT_KM, MAX_HEIGHT_KM = 15.0, 180.0
# Part of the shorter of the two seen stretches that both cameras must have in common.
MIN_OVERLAP = 0.3

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
