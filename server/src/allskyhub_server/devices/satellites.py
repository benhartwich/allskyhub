"""Visible passes of bright satellites (roadmap C6), for "Heute Nacht".

TLEs from CelesTrak, fetched at most twice a day; positions with SGP4 (TEME), turned into
the camera's horizon with Greenwich sidereal time (precession, nutation and polar motion
are below a tenth of a degree and do not matter for a pass list). A pass is listed when
the satellite is above 10°, lit by the sun and the sky at the camera is dark enough to see
it (sun below -6°).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx
import sgp4.api  # pyright: ignore[reportMissingTypeStubs]

from allskyhub_server.devices import astro

# NORAD id → German name; the brightest objects people look for.
SATELLITES = {25544: "Raumstation ISS", 48274: "Raumstation Tiangong", 20580: "Hubble"}
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={id}&FORMAT=TLE"
TLE_MAX_AGE_S = 12 * 3600
TIMEOUT_S = 4.0
MIN_ALTITUDE = 10.0
SKY_DARK = -6.0
STEP = dt.timedelta(seconds=20)
EARTH_RADIUS_KM = 6378.137
# sgp4 has no type hints; these are the three calls used here.
_SGP4: Any = cast(Any, sgp4.api)


def _jday(t: dt.datetime) -> tuple[float, float]:
    jd, fr = _SGP4.jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
    return float(jd), float(fr)


def _propagate(sat: Any, t: dt.datetime) -> tuple[int, tuple[float, float, float]]:
    jd, fr = _jday(t)
    err, r, _ = sat.sgp4(jd, fr)
    return int(err), (float(r[0]), float(r[1]), float(r[2]))


_F = 1 / 298.257223563


@dataclass(frozen=True)
class Pass:
    name: str
    start: dt.datetime  # first moment visible
    end: dt.datetime
    max_altitude: float
    start_azimuth: float
    end_azimuth: float
    top_azimuth: float


def _observer_ecef(lat: float, lon: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    e2 = _F * (2 - _F)
    n = EARTH_RADIUS_KM / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return (n * math.cos(la) * math.cos(lo), n * math.cos(la) * math.sin(lo),
            n * (1 - e2) * math.sin(la))  # fmt: skip


def _gmst_rad(t: dt.datetime) -> float:
    d = (t - dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)).total_seconds() / 86400
    return math.radians((280.46061837 + 360.98564736629 * d) % 360)


def _sun_unit(t: dt.datetime) -> tuple[float, float, float]:
    d = (t - dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)).total_seconds() / 86400
    lon = math.radians(astro._sun_ecliptic(d))  # pyright: ignore[reportPrivateUsage]
    eps = math.radians(23.439)
    return (math.cos(lon), math.cos(eps) * math.sin(lon), math.sin(eps) * math.sin(lon))


def _sunlit(r: tuple[float, float, float], sun: tuple[float, float, float]) -> bool:
    """Cylindrical Earth shadow."""
    along = r[0] * sun[0] + r[1] * sun[1] + r[2] * sun[2]
    if along > 0:
        return True
    perp2 = sum(c * c for c in r) - along * along
    return perp2 > EARTH_RADIUS_KM**2


def _look(
    r_teme: tuple[float, float, float], t: dt.datetime, lat: float, lon: float
) -> tuple[float, float]:
    """Azimuth (0 = north, clockwise) and altitude in degrees."""
    g = _gmst_rad(t)
    x = r_teme[0] * math.cos(g) + r_teme[1] * math.sin(g)
    y = -r_teme[0] * math.sin(g) + r_teme[1] * math.cos(g)
    z = r_teme[2]
    ox, oy, oz = _observer_ecef(lat, lon)
    dx, dy, dz = x - ox, y - oy, z - oz
    la, lo = math.radians(lat), math.radians(lon)
    east = -math.sin(lo) * dx + math.cos(lo) * dy
    north = (-math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy
             + math.cos(la) * dz)  # fmt: skip
    up = math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy + math.sin(la) * dz
    alt = math.degrees(math.atan2(up, math.hypot(east, north)))
    az = math.degrees(math.atan2(east, north)) % 360
    return az, alt


def passes(
    tles: dict[int, tuple[str, str]],
    lat: float,
    lon: float,
    start: dt.datetime,
    end: dt.datetime,
) -> list[Pass]:
    found: list[Pass] = []
    for norad, (line1, line2) in tles.items():
        sat: Any = _SGP4.Satrec.twoline2rv(line1, line2)
        current: list[tuple[dt.datetime, float, float]] = []
        t = start
        while t <= end:
            err, pos = _propagate(sat, t)
            visible = False
            if err == 0:
                az, alt = _look(pos, t, lat, lon)
                visible = (
                    alt >= MIN_ALTITUDE
                    and _sunlit(pos, _sun_unit(t))
                    and astro.sun_altitude(t, lat, lon) < SKY_DARK
                )
                if visible:
                    current.append((t, az, alt))
            else:
                break  # the orbit has decayed or the TLE is broken
            if not visible and current:
                top = max(current, key=lambda p: p[2])
                found.append(Pass(SATELLITES.get(norad, str(norad)), current[0][0],
                                  current[-1][0], top[2], current[0][1], current[-1][1],
                                  top[1]))  # fmt: skip
                current = []
            t += STEP
    return sorted(found, key=lambda p: p.start)


class TleCache:
    """CelesTrak asks to fetch rarely; twice a day is plenty for a pass list."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._tles: dict[int, tuple[str, str]] = {}
        self._at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> dict[int, tuple[str, str]]:
        if self._at and time.monotonic() - self._at < TLE_MAX_AGE_S:
            return self._tles
        async with self._lock:
            if self._at and time.monotonic() - self._at < TLE_MAX_AGE_S:
                return self._tles
            self._at = time.monotonic()
            client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
            try:
                for norad in SATELLITES:
                    try:
                        r = await client.get(TLE_URL.format(id=norad), timeout=TIMEOUT_S)
                        r.raise_for_status()
                    except httpx.HTTPError:
                        continue
                    lines = [x.strip() for x in r.text.splitlines() if x.strip()]
                    l1 = next((x for x in lines if x.startswith("1 ")), None)
                    l2 = next((x for x in lines if x.startswith("2 ")), None)
                    if l1 and l2:
                        self._tles[norad] = (l1, l2)
            finally:
                if self._client is None:
                    await client.aclose()
            return self._tles
