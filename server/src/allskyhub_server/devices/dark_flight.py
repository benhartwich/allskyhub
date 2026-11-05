"""Where meteorites land: the dark flight after the glow, with the winds aloft (package C).

Below the end of the glow a piece no longer burns; air slows it and the wind carries it
while it falls. Each piece is a stony sphere (``DENSITY``) with the drag of a sphere at its
Mach number, in the US standard atmosphere, starting at the end of the glow along the
direction of flight. Its speed there is not measured (one exposure does not show it, GMN
gives the entry speed), so two typical speeds are tried (``END_SPEEDS``). The masses span
small pieces, which the wind carries most, to heavy ones, which fly farthest
(Benjamin, 2026-11-05). The field on the map holds all their landing places, widened by
the error of the end (devices/triangulation.py) and a part of each piece's wind drift for
the error of the wind.

Flat ground at ``GROUND_KM`` around the end, local east-north-up coordinates: the pieces
land within some 100 km, where the Earth's curve does not matter for a first search area.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from allskyhub_server.devices import fireball, triangulation

DENSITY = 3500.0  # kg/m³, stony meteorites
MASSES_KG = (0.01, 0.1, 1.0, 10.0)
END_SPEEDS_KMS = (2.5, 4.0)  # at the end of the glow; luminous flight ends near 3 km/s
GROUND_KM = 0.4
G = 9.81
# Part of a piece's drift by the wind that the field grows by for the wind's own error.
WIND_ERROR = 0.2
MAX_STEPS = 200_000

# (height km, wind east m/s, wind north m/s), from the ground up.
Profile = list[tuple[float, float, float]]


def air(height_km: float) -> tuple[float, float]:
    """US standard atmosphere 1976 up to 51 km: density (kg/m³) and speed of sound (m/s)."""
    layers = (  # base height km, base temperature K, lapse K/km, base pressure Pa
        (0.0, 288.15, -6.5, 101325.0),
        (11.0, 216.65, 0.0, 22632.1),
        (20.0, 216.65, 1.0, 5474.89),
        (32.0, 228.65, 2.8, 868.02),
        (47.0, 270.65, 0.0, 110.91),
    )
    h = max(0.0, min(height_km, 51.0))
    base, t0, lapse, p0 = next(layer for layer in reversed(layers) if h >= layer[0])
    t = t0 + lapse * (h - base)
    if lapse == 0:
        p = p0 * math.exp(-G * 0.0289644 * (h - base) * 1000 / (8.31446 * t0))
    else:
        p = p0 * (t0 / t) ** (G * 0.0289644 / (8.31446 * lapse / 1000))
    return p / (287.053 * t), math.sqrt(1.4 * 287.053 * t)


def drag_coefficient(mach: float) -> float:
    """A sphere: about 0.5 well below the speed of sound, about 1 above it."""
    if mach <= 0.8:
        return 0.5
    if mach >= 1.2:
        return 1.0
    return 0.5 + (mach - 0.8) / 0.4 * 0.5


def wind_at(profile: Profile, height_km: float) -> tuple[float, float]:
    if not profile:
        return 0.0, 0.0
    if height_km <= profile[0][0]:
        return profile[0][1], profile[0][2]
    for (h1, u1, v1), (h2, u2, v2) in itertools.pairwise(profile):
        if height_km <= h2:
            f = (height_km - h1) / (h2 - h1) if h2 > h1 else 0.0
            return u1 + f * (u2 - u1), v1 + f * (v2 - v1)
    return profile[-1][1], profile[-1][2]


def fall(
    mass_kg: float, start_km: float, velocity: tuple[float, float, float], profile: Profile
) -> tuple[float, float, float]:
    """Where a piece lands from (0, 0, start) with ``velocity`` (east, north, up, m/s):
    east and north (m) and how far the wind alone moved it (m, rough)."""
    radius = (3 * mass_kg / (4 * math.pi * DENSITY)) ** (1 / 3)
    k = math.pi * radius**2 / (2 * mass_kg)  # drag per (Cd rho |v|^2)
    x, y, z = 0.0, 0.0, start_km * 1000
    vx, vy, vz = velocity
    drift = 0.0
    ground = GROUND_KM * 1000
    for _ in range(MAX_STEPS):
        if z <= ground:
            break
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        dt = 0.02 if speed > 1000 else 0.1 if speed > 200 else 0.5
        u, v = wind_at(profile, z / 1000)
        rho, sound = air(z / 1000)
        rx, ry, rz = vx - u, vy - v, vz
        rel = math.sqrt(rx * rx + ry * ry + rz * rz)
        a = k * drag_coefficient(rel / sound) * rho * rel
        vx -= a * rx * dt
        vy -= a * ry * dt
        vz -= (a * rz + G) * dt
        x += vx * dt
        y += vy * dt
        z += vz * dt
        drift += math.hypot(u, v) * dt * min(1.0, 200 / max(speed, 1.0))
    return x, y, drift


@dataclass(frozen=True)
class Landing:
    mass_kg: float
    speed_kms: float
    lat: float
    lon: float
    km_from_end: float
    azimuth_deg: float  # from the end of the glow


@dataclass(frozen=True)
class StrewnField:
    landings: list[Landing]
    outline: list[tuple[float, float]]  # lat, lon, closed


def _direction(
    high: tuple[float, float, float], low: tuple[float, float, float]
) -> tuple[float, float, float]:
    """Unit direction of flight at the low end, east-north-up."""
    a, b = triangulation.ecef(*high), triangulation.ecef(*low)
    d = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    la, lo = math.radians(low[0]), math.radians(low[1])
    east = (-math.sin(lo), math.cos(lo), 0.0)
    north = (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la))
    up = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    e, n, u = (sum(d[i] * axis[i] for i in range(3)) for axis in (east, north, up))
    norm = math.sqrt(e * e + n * n + u * u)
    return e / norm, n / norm, u / norm


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convex hull (monotone chain), counter-clockwise, closed."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return [*pts, pts[0]] if pts else []

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    ring = lower[:-1] + upper[:-1]
    return [*ring, ring[0]]


def strewn_field(
    high: tuple[float, float, float],
    low: tuple[float, float, float],
    profile: Profile,
    end_err_km: float = 0.0,
) -> StrewnField:
    """All pieces from the end of the glow ``low`` (lat, lon, km), flying from ``high``."""
    de, dn, du = _direction(high, low)
    landings: list[Landing] = []
    ring: list[tuple[float, float]] = []
    for speed in END_SPEEDS_KMS:
        v = speed * 1000
        for mass in MASSES_KG:
            east, north, drift = fall(mass, low[2], (de * v, dn * v, du * v), profile)
            km = math.hypot(east, north) / 1000
            azimuth = math.degrees(math.atan2(east, north)) % 360
            lat, lon = fireball.destination(low[0], low[1], azimuth, km)
            landings.append(Landing(mass, speed, lat, lon, km, azimuth))
            grow = end_err_km + WIND_ERROR * drift / 1000
            # A circle of the error around each landing, as 12 points, for the hull.
            ring.extend(
                fireball.destination(lat, lon, bearing, grow) if grow > 0 else (lat, lon)
                for bearing in range(0, 360, 30)
            )
    # The hull in plane coordinates (lon scaled by cos lat), back to lat, lon.
    scale = math.cos(math.radians(low[0]))
    hull = _hull([(lon * scale, lat) for lat, lon in ring])
    return StrewnField(landings, [(lat, x / scale) for x, lat in hull])
