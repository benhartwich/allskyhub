"""Objects for "Ziel frei?" (roadmap H4)."""

from __future__ import annotations

import datetime as dt
import math

from allskyhub_sky.objects import catalogue, planet_radec, radec

AT = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)


def _sep(a: tuple[float, float], b: tuple[float, float]) -> float:
    ra1, d1, ra2, d2 = (math.radians(v) for v in (*a, *b))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(min(1.0, c)))


def test_planets_match_jpl_horizons() -> None:
    """Astrometric positions from JPL Horizons for 2026-10-15 00:00 UTC."""
    horizons = {
        "mars": (132.05179, 19.15524),
        "jupiter": (144.16158, 14.91340),
        "saturn": (10.34941, 1.50638),
    }
    for planet, expected in horizons.items():
        assert _sep(planet_radec(planet, AT), expected) < 0.1, planet


def test_catalogue() -> None:
    items = {o.key: o for o in catalogue()}
    assert len(items) == 110 + 7
    assert items["M31"].label == "M31 Andromedagalaxie"
    assert items["M2"].label == "M2"
    assert items["jupiter"].kind == "planet"
    assert radec("M42", AT) == (83.85, -5.45)
    assert radec("nope", AT) is None
