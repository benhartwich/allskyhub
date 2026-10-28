"""Planets of a night, moon–planet meetings and eclipses for "Himmel" in the app.

Low precision on purpose (``allskyhub_sky``: the sun to arc minutes, the moon to about a
quarter degree, the planets to a fraction of a degree): enough to say which planets are
up and when, that the moon passes a planet, and whether an eclipse can be seen at a place.
Eclipses come from the moon's ecliptic latitude at a full or new moon; whether a solar
eclipse reaches a place from the apparent distance of sun and moon there.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from allskyhub_sky import alt_az, precess, sidereal_deg
from allskyhub_sky.astro import (
    moon_altitude,
    moon_ecliptic_latitude,
    moon_radec,
    sun_altitude,
    sun_radec,
)
from allskyhub_sky.objects import PLANETS, PLANETS_EN, planet_radec

# Planets seen with the eye; Uranus and Neptune need a telescope.
NAKED_EYE = ("mercury", "venus", "mars", "jupiter", "saturn")
BRIGHT = ("venus", "mars", "jupiter", "saturn")
MIN_ALTITUDE = 10.0  # degrees: lower ones hide in haze and behind trees
DARK_ENOUGH = -6.0  # sun altitude: bright planets show from civil dusk
STEP = dt.timedelta(minutes=15)
MEETING_DEG = 4.0
# Ecliptic latitude of the moon at a full moon: below these, the moon touches the penumbra
# or the umbra (rough means; the limits vary with the distances).
PENUMBRAL_DEG, PARTIAL_DEG = 1.55, 1.0
LUNAR_SPAN_MIN = 75  # the umbral phase lasts up to about 3½ hours
SOLAR_DEG = 1.6  # at a new moon: a solar eclipse somewhere on earth
SOLAR_TOUCH_DEG = 0.5  # sun and moon discs touch (radii about 0.27° and 0.26°)


def planet_name(key: str, english: bool) -> str:
    return (PLANETS_EN if english else PLANETS)[key]


def planet_altaz(key: str, at: dt.datetime, lat: float, lon: float) -> tuple[float, float]:
    ra, dec = precess(*planet_radec(key, at), at)
    return alt_az(ra, dec, sidereal_deg(at, lon), lat)


def separation(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Angle between two (RA, Dec) or (alt, az) pairs, degrees."""
    (x1, y1), (x2, y2) = a, b
    c = math.sin(math.radians(y1)) * math.sin(math.radians(y2)) + math.cos(
        math.radians(y1)
    ) * math.cos(math.radians(y2)) * math.cos(math.radians(x1 - x2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


@dataclass(frozen=True)
class PlanetWindow:
    key: str
    start: dt.datetime
    end: dt.datetime
    best: dt.datetime  # highest point while it is dark enough
    altitude: float
    azimuth: float


def planets(start: dt.datetime, end: dt.datetime, lat: float, lon: float) -> list[PlanetWindow]:
    """Naked-eye planets at least ``MIN_ALTITUDE`` high in [start, end) while the sun is
    below ``DARK_ENOUGH``; one window per planet (the longest), by start."""
    times: list[dt.datetime] = []
    t = start
    while t < end:
        if sun_altitude(t, lat, lon) < DARK_ENOUGH:
            times.append(t)
        t += STEP
    out: list[PlanetWindow] = []
    for key in NAKED_EYE:
        runs: list[list[tuple[dt.datetime, float, float]]] = []
        current: list[tuple[dt.datetime, float, float]] = []
        for i, t in enumerate(times):
            alt, az = planet_altaz(key, t, lat, lon)
            gap = i > 0 and t - times[i - 1] > STEP
            if alt >= MIN_ALTITUDE and not gap:
                current.append((t, alt, az))
            elif current:
                runs.append(current)
                current = [(t, alt, az)] if alt >= MIN_ALTITUDE else []
            elif alt >= MIN_ALTITUDE:
                current = [(t, alt, az)]
        if current:
            runs.append(current)
        if not runs:
            continue
        run = max(runs, key=len)
        top = max(run, key=lambda p: p[1])
        out.append(PlanetWindow(key, run[0][0], run[-1][0] + STEP, top[0], top[1], top[2]))
    return sorted(out, key=lambda w: w.start)


@dataclass(frozen=True)
class Meeting:
    key: str
    at: dt.datetime
    degrees: float


def moon_meetings(
    nights: list[tuple[dt.datetime, dt.datetime]], lat: float, lon: float
) -> list[Meeting]:
    """The moon within ``MEETING_DEG`` of a bright planet, both up in the dark; the
    closest moment per night and planet."""
    found: list[Meeting] = []
    for start, end in nights:
        best: dict[str, Meeting] = {}
        t = start
        while t < end:
            if sun_altitude(t, lat, lon) < -12 and moon_altitude(t, lat, lon) > MIN_ALTITUDE:
                moon = moon_radec(t)
                for key in BRIGHT:
                    if planet_altaz(key, t, lat, lon)[0] < MIN_ALTITUDE:
                        continue
                    d = separation(moon, precess(*planet_radec(key, t), t))
                    if d < MEETING_DEG and (key not in best or d < best[key].degrees):
                        best[key] = Meeting(key, t, d)
            t += dt.timedelta(hours=1)
        found += best.values()
    return sorted(found, key=lambda m: m.at)


@dataclass(frozen=True)
class Eclipse:
    kind: str  # "lunar" or "solar"
    depth: str  # lunar: "umbral" or "penumbral"; solar: "partial" (at least, here)
    at: dt.datetime  # greatest eclipse (lunar), closest approach here (solar)


def lunar_eclipse(full_moon: dt.datetime, lat: float, lon: float) -> Eclipse | None:
    """A lunar eclipse at this full moon that the place sees: the moon above the horizon
    within ``LUNAR_SPAN`` of the greatest eclipse. The depth is "umbral" (partial or total;
    the position is too rough to tell them apart) or "penumbral"."""
    beta = abs(moon_ecliptic_latitude(full_moon))
    if beta >= PENUMBRAL_DEG:
        return None
    steps = range(-LUNAR_SPAN_MIN, LUNAR_SPAN_MIN + 1, 15)
    if not any(moon_altitude(full_moon + dt.timedelta(minutes=m), lat, lon) > 0 for m in steps):
        return None
    return Eclipse("lunar", "umbral" if beta < PARTIAL_DEG else "penumbral", full_moon)


def solar_eclipse(new_moon: dt.datetime, lat: float, lon: float) -> Eclipse | None:
    """A solar eclipse at this new moon that reaches the place with the sun up."""
    if abs(moon_ecliptic_latitude(new_moon)) >= SOLAR_DEG:
        return None
    best: tuple[float, dt.datetime] | None = None
    t = new_moon - dt.timedelta(hours=5)
    while t < new_moon + dt.timedelta(hours=5):
        sun_alt = sun_altitude(t, lat, lon)
        if sun_alt > 0:
            lst = sidereal_deg(t, lon)
            sun = alt_az(*sun_radec(t), lst, lat)
            moon = (moon_altitude(t, lat, lon), alt_az(*moon_radec(t), lst, lat)[1])
            d = separation((sun[1], sun[0]), (moon[1], moon[0]))
            if best is None or d < best[0]:
                best = (d, t)
        t += dt.timedelta(minutes=5)
    if best is None or best[0] >= SOLAR_TOUCH_DEG:
        return None
    return Eclipse("solar", "partial", best[1])
