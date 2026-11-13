"""More layers over the live image (draft 1, 2026-11-13): the brightest named stars, an
altitude grid, moon and planets, and the bright satellites. Constellations (H1) and
aircraft (H3) stay in ``sky_overlay``.

Everything is drawn with the camera's lens and orientation (SPEC §4.8, §6.3), in pixels of
the lens's frame, like the constellations.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from functools import cache
from typing import Any, cast

from allskyhub_server.devices import bright_satellites
from allskyhub_server.devices.satellite_match import Catalogue, epoch, look_all
from allskyhub_server.devices.satellites import MIN_ALTITUDE
from allskyhub_server.devices.sky_overlay import orientation
from allskyhub_sky import alt_az, precess, project, sidereal_deg
from allskyhub_sky.astro import moon_illumination, moon_radec
from allskyhub_sky.objects import PLANETS, planet_radec
from allskyhub_solve.solve import CATALOGUE as STAR_CATALOGUE

STAR_MIN_ALT = 8.0
STAR_MAX_MAG = 1.6  # about the 25 brightest named stars, so the picture stays readable
PLANET_MIN_ALT = 0.0
GRID_ALTITUDES = (30.0, 60.0)
GRID_STEP_DEG = 5.0
CARDINAL_ALT = 6.0
SAT_TRAIL = dt.timedelta(seconds=60)
SAT_TRAIL_STEP = dt.timedelta(seconds=10)
# Elements further than this from a frame's time are left out: SGP4 drifts by hundreds of
# km for the ISS within weeks (drag, reboosts), which would draw passes that never were.
# The live image is never more than a day off (fetched daily); eight days keep the frames
# of the last week's nights (sky_history, allsky review 2026-11-13). The ISS pass checked
# against Skyfield in test_satellites.py lies 7.0 days after its epoch and is right to 1°.
MAX_EPOCH_DAYS = 8.0


@dataclass(frozen=True)
class Mark:
    x: float
    y: float
    text: str
    trail: str = ""  # SVG path (satellites: the last minute)


@dataclass(frozen=True)
class Layers:
    width: int
    height: int
    stars: list[Mark] = field(default_factory=lambda: list[Mark]())
    grid: list[str] = field(default_factory=lambda: list[str]())  # SVG paths
    grid_labels: list[Mark] = field(default_factory=lambda: list[Mark]())
    cardinals: list[Mark] = field(default_factory=lambda: list[Mark]())  # N/O/S/W of the grid
    planets: list[Mark] = field(default_factory=lambda: list[Mark]())
    satellites: list[Mark] = field(default_factory=lambda: list[Mark]())

    @property
    def font(self) -> float:
        return round(self.width / 80, 1)


@cache
def _named_stars() -> list[tuple[str, float, float, float]]:
    """(name, ra, dec, vmag) of the named Hipparcos stars the camera's solver uses."""
    data: Any = json.loads(STAR_CATALOGUE.read_text())
    out: list[tuple[str, float, float, float]] = []
    for row in cast(list[list[Any]], data["stars"]):
        if len(row) > 3 and isinstance(row[3], str):
            out.append((row[3].title(), float(row[0]), float(row[1]), float(row[2])))
    return out


def build(
    status: dict[str, Any] | None,
    at: dt.datetime,
    satellites: Catalogue | None = None,
) -> Layers | None:
    """The layers for an image taken ``at``; None without a usable lens or place."""
    found = orientation(status)
    where = bright_satellites.location(status)
    if found is None or found.lens is None or where is None:
        return None
    lat, lon = where
    lens = found.lens

    def pixel(alt: float, az: float) -> tuple[float, float]:
        return project(alt, az, lens, found.north_deg, found.mirrored)

    lst = sidereal_deg(at, lon)
    result = Layers(lens.width, lens.height)

    for name, ra, dec, mag in _named_stars():
        if mag > STAR_MAX_MAG:
            continue
        alt, az = alt_az(*precess(ra, dec, at), lst, lat)
        if alt >= STAR_MIN_ALT:
            result.stars.append(Mark(*pixel(alt, az), name))

    for ring in GRID_ALTITUDES:
        points = [pixel(ring, i * GRID_STEP_DEG) for i in range(int(360 / GRID_STEP_DEG) + 1)]
        result.grid.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in points))
        result.grid_labels.append(Mark(*pixel(ring, 0.0), f"{ring:.0f}°"))
    for letter, az in (("N", 0.0), ("O", 90.0), ("S", 180.0), ("W", 270.0)):
        result.cardinals.append(Mark(*pixel(CARDINAL_ALT, az), letter))
    for az in range(0, 360, 45):
        a, b = pixel(0.0, float(az)), pixel(80.0, float(az))
        result.grid.append(f"M{a[0]:.1f} {a[1]:.1f}L{b[0]:.1f} {b[1]:.1f}")

    moon_alt, moon_az = alt_az(*moon_radec(at), lst, lat)
    if moon_alt >= PLANET_MIN_ALT:
        lit = round(moon_illumination(at) * 100)
        result.planets.append(Mark(*pixel(moon_alt, moon_az), f"Mond {lit} %"))
    for key, name in PLANETS.items():
        if key in ("uranus", "neptune"):
            continue  # not visible to an all-sky camera
        alt, az = alt_az(*precess(*planet_radec(key, at), at), lst, lat)
        if alt >= PLANET_MIN_ALT:
            result.planets.append(Mark(*pixel(alt, az), name))

    if satellites is not None and satellites.array is not None and len(satellites):
        result.satellites.extend(_satellites(satellites, lat, lon, at, pixel))
    return result


def _satellites(
    catalogue: Catalogue,
    lat: float,
    lon: float,
    at: dt.datetime,
    pixel: Any,
) -> list[Mark]:
    """The bright satellites in the picture at ``at`` (sunlit, at least 10° up), each with
    the path of its last minute."""
    steps = int(SAT_TRAIL / SAT_TRAIL_STEP)
    times = [at - SAT_TRAIL + i * SAT_TRAIL_STEP for i in range(steps + 1)]
    ok, az, alt, _, lit = look_all(catalogue.array, len(catalogue), lat, lon, times)
    marks: list[Mark] = []
    for k in range(len(catalogue)):
        a, z = float(alt[k, -1]), float(az[k, -1])
        if not ok[k, -1] or not lit[k, -1] or a < MIN_ALTITUDE:
            continue
        if abs(at - epoch(catalogue.sats[k])) > dt.timedelta(days=MAX_EPOCH_DAYS):
            continue
        points = [
            cast(tuple[float, float], pixel(float(alt[k, j]), float(az[k, j])))
            for j in range(steps + 1)
            if ok[k, j] and float(alt[k, j]) > 0
        ]
        trail = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in points) if len(points) > 1 else ""
        name = bright_satellites.german_name(catalogue.norads[k], catalogue.names[k])
        x, y = cast(tuple[float, float], pixel(a, z))
        if math.isfinite(x) and math.isfinite(y):
            marks.append(Mark(x, y, name, trail))
    return marks
