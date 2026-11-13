"""The bright satellites over a camera, now and tonight (draft 5, 2026-11-13).

CelesTrak's group ``visual``, about 150 of the brightest objects (space stations, Hubble,
large rocket bodies). The hub already keeps it on disk for the satellites among the meteors
(``satellite_match``, SPEC §7.9); this reads the same file and fetches it at most once a day.

Each satellite gets three marks, all estimates without measured brightness:
- *sunlit*: outside the Earth's shadow (a cylinder);
- *naked eye*: sunlit, at least ``MIN_ALTITUDE`` up and the sky at the camera dark enough
  (sun below ``SKY_DARK``); the group only holds objects bright enough for that;
- *in the picture*: at least ``MIN_ALTITUDE`` up, where an all-sky camera still sees sky
  (the hub does not know the camera's own sky mask).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from allskyhub_server.devices import astro
from allskyhub_server.devices.satellite_match import Catalogue, look_all
from allskyhub_server.devices.satellites import EARTH_RADIUS_KM, MIN_ALTITUDE, SATELLITES, SKY_DARK

if TYPE_CHECKING:
    from allskyhub_server.devices.satellite_match import SatelliteCatalogue

GROUP = "visual"
# The hub's catalogue of this group (set by the app), for drawing on the live image.
current: SatelliteCatalogue | None = None
NIGHT_STEP = dt.timedelta(seconds=30)
# Tonight lists the good passes only: at least this high at their top.
NIGHT_MIN_TOP = 30.0
NIGHT_MAX = 20


def german_name(norad: int, name: str) -> str:
    """CelesTrak's name, with the German one for the famous few and a word for what
    rocket bodies and debris are."""
    if norad in SATELLITES:
        return SATELLITES[norad]
    if "R/B" in name:
        return f"{name} (Raketenstufe)"
    if " DEB" in name:
        return f"{name} (Trümmerteil)"
    return name


@dataclass(frozen=True)
class SatNow:
    norad: int
    name: str
    azimuth: float
    altitude: float
    range_km: float
    height_km: float
    sunlit: bool
    naked_eye: bool

    @property
    def in_picture(self) -> bool:
        return self.altitude >= MIN_ALTITUDE


def now(catalogue: Catalogue | None, lat: float, lon: float, t: dt.datetime) -> list[SatNow]:
    """Every satellite of the catalogue above the horizon at ``t``, highest first."""
    if catalogue is None or catalogue.array is None or not len(catalogue):
        return []
    ok, az, alt, dist, lit = look_all(catalogue.array, len(catalogue), lat, lon, [t])
    dark = astro.sun_altitude(t, lat, lon) < SKY_DARK
    out: list[SatNow] = []
    for k in range(len(catalogue)):
        if not ok[k, 0] or alt[k, 0] < 0:
            continue
        a, d, sunlit = float(alt[k, 0]), float(dist[k, 0]), bool(lit[k, 0])
        out.append(
            SatNow(
                norad=catalogue.norads[k],
                name=german_name(catalogue.norads[k], catalogue.names[k]),
                azimuth=float(az[k, 0]),
                altitude=a,
                range_km=d,
                height_km=_height(d, a),
                sunlit=sunlit,
                naked_eye=sunlit and dark and a >= MIN_ALTITUDE,
            )
        )
    return sorted(out, key=lambda s: -s.altitude)


def _height(range_km: float, altitude: float) -> float:
    """Height above the ground from the slant range and the altitude angle (spherical
    Earth, observer at sea level)."""
    r = EARTH_RADIUS_KM
    e = math.radians(altitude)
    return math.sqrt(r * r + range_km * range_km + 2 * r * range_km * math.sin(e)) - r


@dataclass(frozen=True)
class SatPass:
    norad: int
    name: str
    start: dt.datetime  # first moment seen with the naked eye
    end: dt.datetime
    top_altitude: float
    top_azimuth: float
    start_azimuth: float
    end_azimuth: float


def night_passes(
    catalogue: Catalogue | None,
    lat: float,
    lon: float,
    start: dt.datetime,
    end: dt.datetime,
) -> list[SatPass]:
    """The naked-eye passes from ``start`` to ``end`` that reach ``NIGHT_MIN_TOP``, in time
    order, at most ``NIGHT_MAX`` (the highest kept)."""
    if catalogue is None or catalogue.array is None or not len(catalogue) or end <= start:
        return []
    steps = int((end - start) / NIGHT_STEP) + 1
    times = [start + i * NIGHT_STEP for i in range(steps)]
    ok, az, alt, _, lit = look_all(catalogue.array, len(catalogue), lat, lon, times)
    dark = [astro.sun_altitude(t, lat, lon) < SKY_DARK for t in times]
    found: list[SatPass] = []
    for k in range(len(catalogue)):
        seen = [
            bool(ok[k, j]) and bool(lit[k, j]) and dark[j] and float(alt[k, j]) >= MIN_ALTITUDE
            for j in range(steps)
        ]
        j = 0
        while j < steps:
            if not seen[j]:
                j += 1
                continue
            first = j
            while j < steps and seen[j]:
                j += 1
            last = j - 1
            top = max(range(first, last + 1), key=lambda i: float(alt[k, i]))
            if float(alt[k, top]) < NIGHT_MIN_TOP:
                continue
            found.append(
                SatPass(
                    norad=catalogue.norads[k],
                    name=german_name(catalogue.norads[k], catalogue.names[k]),
                    start=times[first],
                    end=times[last],
                    top_altitude=float(alt[k, top]),
                    top_azimuth=float(az[k, top]),
                    start_azimuth=float(az[k, first]),
                    end_azimuth=float(az[k, last]),
                )
            )
    best = sorted(found, key=lambda p: -p.top_altitude)[:NIGHT_MAX]
    return sorted(best, key=lambda p: p.start)


def location(status: dict[str, Any] | None) -> tuple[float, float] | None:
    """The camera's place from its settings (SPEC §6.3), or None."""
    settings = (status or {}).get("settings")
    if not isinstance(settings, dict):
        return None
    lat, lon = settings.get("latitude"), settings.get("longitude")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return float(lat), float(lon)


Filter = Literal["jetzt", "oben", "nacht"]


@dataclass(frozen=True)
class View:
    """What the satellites card (web) and the app show for one filter."""

    where: tuple[float, float] | None
    loaded: bool
    now_list: list[SatNow]
    passes: list[SatPass]


async def view(
    catalogue: Catalogue | None, status: dict[str, Any] | None, f: Filter, at: dt.datetime
) -> View:
    """``jetzt``: naked eye now; ``oben``: above the horizon now; ``nacht``: tonight's good
    passes still ahead. Computed off the event loop."""
    from allskyhub_server.devices import tonight

    where = location(status)
    if where is None or catalogue is None:
        return View(where, catalogue is not None, [], [])
    lat, lon = where
    if f == "nacht":
        reported = cast(dict[str, Any], (status or {}).get("settings") or {})
        night = tonight.night_now(at, lat, lon, tonight.zone_for(reported.get("timezone")))
        if not (night.sunset and night.sunrise):
            return View(where, True, [], [])
        start = max(night.sunset, at)
        passes = await asyncio.to_thread(night_passes, catalogue, lat, lon, start, night.sunrise)
        return View(where, True, [], passes)
    found = await asyncio.to_thread(now, catalogue, lat, lon, at)
    return View(where, True, [s for s in found if s.naked_eye] if f == "jetzt" else found, [])
