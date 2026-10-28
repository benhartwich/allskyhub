"""Reporting the sky brightness by eye (Globe at Night style): the constellation to look
at, the star charts to compare with, and what the faintest stars seen mean.

The charts show the stars as the constellation stands now at the place: a gnomonic
projection around its label point in the horizon frame, x to the right and y up toward
the zenith, so the picture matches the sky. Each chart k shows the stars down to
magnitude k; the reporter picks the one that looks like the sky.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from allskyhub_sky import alt_az, constellations, precess, sidereal_deg

STARS = Path(__file__).parent / "data" / "report_stars.json"
# The order to try: Globe at Night's choices first, then all-year northern ones, then the
# southern sky.
ORDER = ("Ori", "Leo", "Boo", "Cyg", "Peg", "Per", "UMa", "Cas", "Sco", "Cru")
MIN_ALTITUDE = 35.0  # lower stars dim in the haze and would make the sky look worse
CHART_LIMITS = (2.0, 3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0)
SPAN_DEG = 28.0  # the chart shows this much sky across


def _stars() -> dict[str, list[list[float]]]:
    data: Any = json.loads(STARS.read_text())
    return cast(dict[str, list[list[float]]], data)


@dataclass(frozen=True)
class Chart:
    key: str
    name: str
    altitude: float
    azimuth: float
    # (x, y, magnitude) with x, y in degrees on the tangent plane, y toward the zenith.
    stars: list[tuple[float, float, float]]


def _tangent(alt: float, az: float, alt0: float, az0: float) -> tuple[float, float] | None:
    """Gnomonic projection of (alt, az) around (alt0, az0), degrees."""
    r = math.radians
    a, z, a0, z0 = r(alt), r(az), r(alt0), r(az0)
    cos_c = math.sin(a0) * math.sin(a) + math.cos(a0) * math.cos(a) * math.cos(z - z0)
    if cos_c <= 0.1:
        return None
    x = math.cos(a) * math.sin(z - z0) / cos_c
    y = (math.cos(a0) * math.sin(a) - math.sin(a0) * math.cos(a) * math.cos(z - z0)) / cos_c
    return math.degrees(x), math.degrees(y)


def chart(lat: float, lon: float, at: dt.datetime, *, latin: bool = False) -> Chart | None:
    """The first constellation of ``ORDER`` at least ``MIN_ALTITUDE`` high, with its stars
    as seen now; None when none is high enough."""
    lst = sidereal_deg(at, lon)
    names = constellations(latin=latin)
    stars = _stars()
    for key in ORDER:
        name, ra, dec = names[key]
        alt0, az0 = alt_az(*precess(ra, dec, at), lst, lat)
        if alt0 < MIN_ALTITUDE:
            continue
        out: list[tuple[float, float, float]] = []
        for sra, sdec, mag in stars[key]:
            alt, az = alt_az(*precess(sra, sdec, at), lst, lat)
            p = _tangent(alt, az, alt0, az0)
            if p is not None and abs(p[0]) <= SPAN_DEG / 2 and abs(p[1]) <= SPAN_DEG / 2:
                out.append((round(p[0], 2), round(p[1], 2), mag))
        return Chart(key, name, round(alt0, 1), round(az0, 1), out)
    return None


def sqm_from_limit(limit: float) -> float:
    """Sky brightness (mag/arcsec²) from the naked-eye limiting magnitude, after the usual
    conversion (Schaefer 1990, as used by Globe at Night); rough, like the method."""
    limit = min(limit, 7.0)
    # The natural night sky is no darker than about 22 mag/arcsec².
    return round(min(22.0, 21.58 - 5 * math.log10(10 ** (1.586 - limit / 5) - 1)), 2)


# Lower bound (mag/arcsec²) → German and English label; like the map's classes.
SKY_CLASSES = (
    (21.7, "sehr dunkler Himmel", "very dark sky"),
    (21.3, "dunkler Landhimmel", "dark rural sky"),
    (20.8, "Landhimmel", "rural sky"),
    (20.0, "Stadtrand", "suburban edge"),
    (19.0, "Vorstadthimmel", "suburban sky"),
    (0.0, "Stadthimmel", "city sky"),
)


def sky_class(sqm: float, *, english: bool = False) -> str:
    return next(en if english else de for low, de, en in SKY_CLASSES if sqm >= low)
