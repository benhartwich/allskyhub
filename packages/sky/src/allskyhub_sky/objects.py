"""Objects to look for (roadmap H4): the Messier catalogue and the planets.

The planets come from JPL's "Keplerian Elements for Approximate Positions of the Major
Planets" (E. M. Standish, table 1, valid 1800–2050, public domain): a few arcminutes,
plenty to say whether a planet is above the horizon and behind which clouds. Positions
are J2000; ``precess`` (in ``allskyhub_sky``) moves them to the date.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "data" / "messier.json"
J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)
OBLIQUITY = 23.43928

# a (au), e, I, L, long. perihelion, long. ascending node (degrees), then rates per century.
_ELEMENTS: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "mercury": ((0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
                (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081)),
    "venus": ((0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
              (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418)),
    "earth": ((1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
              (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)),
    "mars": ((1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
             (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343)),
    "jupiter": ((5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
                (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106)),
    "saturn": ((9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
               (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794)),
    "uranus": ((19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503),
               (-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589)),
    "neptune": ((30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574),
                (0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.01262724)),
}  # fmt: skip
PLANETS = {
    "mercury": "Merkur",
    "venus": "Venus",
    "mars": "Mars",
    "jupiter": "Jupiter",
    "saturn": "Saturn",
    "uranus": "Uranus",
    "neptune": "Neptun",
}


@dataclass(frozen=True)
class SkyObject:
    """``key`` is "M31" or "jupiter"; ``kind`` a short type ("gx", "gc", "oc", "pn",
    "planet", …)."""

    key: str
    label: str
    kind: str
    mag: float | None


def _heliocentric(planet: str, at: dt.datetime) -> tuple[float, float, float]:
    """Ecliptic J2000 coordinates in au."""
    t = (at - J2000).total_seconds() / 86400.0 / 36525.0
    base, rate = _ELEMENTS[planet]
    a, e, inc, mean_lon, peri, node = (b + r * t for b, r in zip(base, rate, strict=True))
    w = peri - node
    m = (mean_lon - peri + 180.0) % 360.0 - 180.0
    ecc = math.radians(m) + e * math.sin(math.radians(m))
    for _ in range(10):
        ecc -= (ecc - e * math.sin(ecc) - math.radians(m)) / (1.0 - e * math.cos(ecc))
    xp = a * (math.cos(ecc) - e)
    yp = a * math.sqrt(1.0 - e * e) * math.sin(ecc)
    cw, sw = math.cos(math.radians(w)), math.sin(math.radians(w))
    cn, sn = math.cos(math.radians(node)), math.sin(math.radians(node))
    ci, si = math.cos(math.radians(inc)), math.sin(math.radians(inc))
    x = (cw * cn - sw * sn * ci) * xp + (-sw * cn - cw * sn * ci) * yp
    y = (cw * sn + sw * cn * ci) * xp + (-sw * sn + cw * cn * ci) * yp
    z = (sw * si) * xp + (cw * si) * yp
    return x, y, z


def planet_radec(planet: str, at: dt.datetime) -> tuple[float, float]:
    """Geocentric right ascension and declination (J2000, degrees) of a planet."""
    px, py, pz = _heliocentric(planet, at)
    ex, ey, ez = _heliocentric("earth", at)
    x, y, z = px - ex, py - ey, pz - ez
    eps = math.radians(OBLIQUITY)
    ye = math.cos(eps) * y - math.sin(eps) * z
    ze = math.sin(eps) * y + math.cos(eps) * z
    ra = math.degrees(math.atan2(ye, x)) % 360.0
    dec = math.degrees(math.atan2(ze, math.hypot(x, ye)))
    return ra, dec


@cache
def _messier() -> dict[str, list[Any]]:
    data: dict[str, list[Any]] = json.loads(DATA.read_text())
    return data


def catalogue() -> list[SkyObject]:
    """Planets first, then M1 … M110."""
    items = [SkyObject(key, name, "planet", None) for key, name in PLANETS.items()]
    for key, (name, _, _, mag, kind) in _messier().items():
        label = f"{key} {name}" if name else key
        items.append(SkyObject(key, label, str(kind), float(mag) if mag is not None else None))
    return items


def radec(key: str, at: dt.datetime) -> tuple[float, float] | None:
    """J2000 position of a catalogue object at ``at`` (the planets move), or None."""
    if key in PLANETS:
        return planet_radec(key, at)
    entry = _messier().get(key)
    return (float(entry[1]), float(entry[2])) if entry else None
