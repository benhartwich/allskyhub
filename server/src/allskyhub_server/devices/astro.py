"""Twilight and moon times for the observing forecast (roadmap C1).

The positions come from ``allskyhub_sky.astro``, which the camera uses too (moon mode):
good to a few arc minutes for the sun and about a quarter degree for the moon, plenty for
twilight and moon rise times to within a minute or two.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from allskyhub_sky.astro import moon_altitude, moon_illumination, moon_waxing, sun_altitude

__all__ = ["moon_altitude", "moon_illumination", "moon_waxing", "sun_altitude"]

# Standard altitudes for rise and set: refraction and semi-diameter (moon: plus parallax,
# which moon_altitude already applies).
SUNRISE_ALT = -0.833
MOONRISE_ALT = -0.583
CIVIL, NAUTICAL, ASTRONOMICAL = -6.0, -12.0, -18.0


def crossings(
    altitude: Callable[[dt.datetime], float],
    level: float,
    start: dt.datetime,
    end: dt.datetime,
    step: dt.timedelta = dt.timedelta(minutes=10),
) -> list[tuple[dt.datetime, bool]]:
    """Times in [start, end) where ``altitude`` crosses ``level``: (time, rising)."""
    found: list[tuple[dt.datetime, bool]] = []
    t0, a0 = start, altitude(start) - level
    while t0 < end:
        t1 = min(t0 + step, end)
        a1 = altitude(t1) - level
        if (a0 < 0) != (a1 < 0):
            lo, hi, alo = t0, t1, a0
            for _ in range(20):  # 10 min / 2^20: well below a second
                mid = lo + (hi - lo) / 2
                am = altitude(mid) - level
                if (am < 0) == (alo < 0):
                    lo, alo = mid, am
                else:
                    hi = mid
            found.append((hi.replace(microsecond=0), a1 > 0))
        t0, a0 = t1, a1
    return found


@dataclass(frozen=True)
class Night:
    """The dark part of a night at a place, all times UTC; None where the sun never gets
    that low (summer at high latitudes)."""

    sunset: dt.datetime | None
    dusk: dt.datetime | None  # end of astronomical twilight
    dawn: dt.datetime | None  # start of astronomical twilight
    sunrise: dt.datetime | None
    moonrise: dt.datetime | None
    moonset: dt.datetime | None
    moon_illumination: float
    moon_waxing: bool


def night_after(noon: dt.datetime, lat: float, lon: float) -> Night:
    """The night following ``noon`` (local noon of the evening's date, as UTC)."""
    end = noon + dt.timedelta(hours=24)

    def sun(t: dt.datetime) -> float:
        return sun_altitude(t, lat, lon)

    def first(level: float, rising: bool) -> dt.datetime | None:
        for t, up in crossings(sun, level, noon, end):
            if up is rising:
                return t
        return None

    def moon(t: dt.datetime) -> float:
        return moon_altitude(t, lat, lon)

    moon_events = crossings(moon, MOONRISE_ALT, noon, end)
    midnight = noon + dt.timedelta(hours=12)
    return Night(
        sunset=first(SUNRISE_ALT, False),
        dusk=first(ASTRONOMICAL, False),
        dawn=first(ASTRONOMICAL, True),
        sunrise=first(SUNRISE_ALT, True),
        moonrise=next((t for t, up in moon_events if up), None),
        moonset=next((t for t, up in moon_events if not up), None),
        moon_illumination=moon_illumination(midnight),
        moon_waxing=moon_waxing(midnight),
    )
