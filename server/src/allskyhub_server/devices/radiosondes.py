"""Weather balloons (radiosondes) over a camera (draft B, Benjamin 2026-11-15), from
SondeHub's open data (CC BY-SA 2.0, credited under the image).

SondeHub learns only the camera's place rounded to ``CELL_DEG`` (about 55 km): the hub asks
once per cell for every camera in it, at most every ``REFRESH``, and only while someone has
a camera's live image open. A balloon heard less than ``MAX_EXTRAPOLATE`` ago is carried
on to the present along its speed and heading; one not heard for ``MAX_AGE`` has landed or
gone silent and is left out. Its direction from the camera is exact: like the satellites
(web/templating.sky_layers), the layer is only for the camera's own viewers.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx

log = logging.getLogger(__name__)

URL = "https://api.v2.sondehub.org/sondes"
CREDIT = "Wetterballons: SondeHub (CC BY-SA 2.0)"
CELL_DEG = 0.5
RADIUS_M = 350_000
REFRESH = 60.0  # seconds between two questions for one cell
MAX_AGE = dt.timedelta(minutes=10)
MAX_EXTRAPOLATE = dt.timedelta(seconds=90)
TRAIL = dt.timedelta(minutes=5)  # drawn back along its speed: where it came from
EARTH_A = 6378137.0
EARTH_E2 = 6.69437999014e-3

current: Radiosondes | None = None


@dataclass(frozen=True)
class Sonde:
    serial: str
    kind: str  # "RS41-SG", "M20", …
    at: dt.datetime  # when it was last heard
    lat: float
    lon: float
    alt_m: float
    vel_h: float  # m/s over ground
    vel_v: float  # m/s, up positive
    heading: float  # degrees from north


def cell(lat: float, lon: float) -> tuple[float, float]:
    """The ``CELL_DEG`` grid point SondeHub is asked for."""
    return (round(lat / CELL_DEG) * CELL_DEG, round(lon / CELL_DEG) * CELL_DEG)


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        out = float(value)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def parse(raw: object) -> list[Sonde]:
    """SondeHub's ``/sondes`` answer: an object of the latest frame per serial."""
    if not isinstance(raw, dict):
        return []
    out: list[Sonde] = []
    for value in cast(dict[str, Any], raw).values():
        if not isinstance(value, dict):
            continue
        item = cast(dict[str, Any], value)
        lat, lon, alt = _num(item.get("lat")), _num(item.get("lon")), _num(item.get("alt"))
        serial, stamp = item.get("serial"), item.get("datetime")
        if lat is None or lon is None or alt is None or not isinstance(serial, str):
            continue
        if not isinstance(stamp, str):
            continue
        try:
            at = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            continue
        kind = item.get("subtype") or item.get("type") or ""
        out.append(Sonde(
            serial=serial[:32], kind=str(kind)[:20], at=at.astimezone(dt.UTC), lat=lat, lon=lon,
            alt_m=alt, vel_h=_num(item.get("vel_h")) or 0.0, vel_v=_num(item.get("vel_v")) or 0.0,
            heading=_num(item.get("heading")) or 0.0,
        ))  # fmt: skip
    return out


def moved(sonde: Sonde, seconds: float) -> tuple[float, float, float]:
    """Where ``sonde`` is ``seconds`` after it was heard (negative: before), along its
    speed and heading on a sphere: (lat, lon, alt_m)."""
    d = sonde.vel_h * seconds / 6371000.0
    h = math.radians(sonde.heading)
    lat = sonde.lat + math.degrees(d * math.cos(h))
    lon = sonde.lon + math.degrees(d * math.sin(h) / max(0.05, math.cos(math.radians(sonde.lat))))
    return lat, lon, max(sonde.alt_m + sonde.vel_v * seconds, 0.0)


def now_at(sonde: Sonde, at: dt.datetime) -> tuple[float, float, float] | None:
    """Its place at ``at``; None when it has not been heard for ``MAX_AGE``."""
    age = at - sonde.at
    if age > MAX_AGE:
        return None
    ahead = min(max(age.total_seconds(), 0.0), MAX_EXTRAPOLATE.total_seconds())
    return moved(sonde, ahead)


def _ecef(lat: float, lon: float, h: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    n = EARTH_A / math.sqrt(1 - EARTH_E2 * math.sin(la) ** 2)
    return (
        (n + h) * math.cos(la) * math.cos(lo),
        (n + h) * math.cos(la) * math.sin(lo),
        (n * (1 - EARTH_E2) + h) * math.sin(la),
    )


def look(
    lat: float, lon: float, elevation_m: float, to: tuple[float, float, float]
) -> tuple[float, float, float]:
    """Altitude and azimuth (degrees) of the point ``to`` (lat, lon, alt_m) seen from the
    camera, and its distance in km."""
    x0, y0, z0 = _ecef(lat, lon, elevation_m)
    x1, y1, z1 = _ecef(*to)
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    la, lo = math.radians(lat), math.radians(lon)
    east = -math.sin(lo) * dx + math.cos(lo) * dy
    north = -math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy + math.cos(la) * dz
    up = math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy + math.sin(la) * dz
    ground = math.hypot(east, north)
    alt = math.degrees(math.atan2(up, ground))
    az = math.degrees(math.atan2(east, north)) % 360.0
    return alt, az, math.sqrt(dx * dx + dy * dy + dz * dz) / 1000.0


class Radiosondes:
    """The balloons per cell, fetched in the background (``warm``) and read without waiting
    (``near``); without a client (tests, switched off) nothing is ever fetched."""

    def __init__(self, client: httpx.AsyncClient | None) -> None:
        self._client = client
        self._cells: dict[tuple[float, float], tuple[float, list[Sonde]]] = {}
        self._busy: set[tuple[float, float]] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def near(self, lat: float, lon: float) -> list[Sonde] | None:
        """The balloons of the camera's cell; None before the first answer."""
        kept = self._cells.get(cell(lat, lon))
        return kept[1] if kept else None

    def put(self, lat: float, lon: float, sondes: list[Sonde]) -> None:
        self._cells[cell(lat, lon)] = (time.monotonic(), sondes)

    def warm(self, lat: float, lon: float) -> None:
        """Ask SondeHub for the camera's cell in the background, unless that was done in
        the last ``REFRESH``."""
        key = cell(lat, lon)
        kept = self._cells.get(key)
        if self._client is None or key in self._busy:
            return
        if kept is not None and time.monotonic() - kept[0] < REFRESH:
            return
        self._busy.add(key)
        task = asyncio.create_task(self._fetch(key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fetch(self, key: tuple[float, float]) -> None:
        client = self._client
        if client is None:
            return
        try:
            answer = await client.get(
                URL,
                params={"lat": f"{key[0]:.1f}", "lon": f"{key[1]:.1f}", "distance": RADIUS_M},
                timeout=15.0,
            )
            answer.raise_for_status()
            self._cells[key] = (time.monotonic(), parse(answer.json()))
        except (httpx.HTTPError, ValueError) as exc:
            log.info("radiosondes not loaded: %s", exc)
            kept = self._cells.get(key)
            # Keep the last list, and wait a full interval before asking again.
            self._cells[key] = (time.monotonic(), kept[1] if kept else [])
        finally:
            self._busy.discard(key)
