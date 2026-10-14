"""Clouds in the next hour from the cameras themselves (roadmap N4).

Weather models know the clouds of the coming hours only roughly; the cameras see them. The
wind at about 3 km (700 hPa, from the forecast) carries what is upwind now over the camera
soon:

- the camera's own cloud map (SPEC §6.3 ``cloud_map``, 8 sectors by 3 altitude bands): at
  an assumed cloud height of 3 km, the low band (15–40°) of the upwind sector looks 4–11 km
  upwind, the middle band (40–65°) 1.5–4 km;
- other cameras in the network upwind (up to ``REACH_KM``), with their overall cover.

``cloud_map`` and ``cloud_cover`` change with every night frame and arrive with the status
about once a minute, raw; the hub keeps the last minutes in memory and takes the median.
Neighbours count only when both owners joined the network (``Device.network``).
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import fireball
from allskyhub_server.devices.tonight import ForecastCache, Hour
from allskyhub_server.models import Device

CLOUD_KM = 3.0
WINDOW = dt.timedelta(minutes=10)
KEEP = dt.timedelta(minutes=20)
REACH_KM = 150.0
# Neighbours this far off the wind's line (km) still say something about what comes.
CROSS_KM = 30.0
# The outlook ends here; beyond it the forecast knows as much.
HORIZON_MIN = 90
STEP_MIN = 15
MIN_WIND_KMH = 5.0
CLEAR, CLOUDY = 0.3, 0.6
# Altitude bands of the cloud map (SPEC §6.3): lower and upper limits.
BANDS = ((15.0, 40.0), (40.0, 65.0), (65.0, 90.0))


@dataclass
class _Sample:
    at: dt.datetime
    cover: float | None
    cells: list[float | None]


@dataclass
class History:
    """The last minutes of each camera's sky measurements (filled from status)."""

    _samples: dict[str, deque[_Sample]] = field(default_factory=dict[str, deque[_Sample]])

    def add(self, device_id: str, sky: dict[str, Any], now: dt.datetime) -> None:
        cover = sky.get("cloud_cover")
        raw = sky.get("cloud_map")
        cells: list[float | None] = []
        if isinstance(raw, list) and len(cast(list[object], raw)) == 24:
            cells = [
                float(v) if isinstance(v, int | float) and 0 <= v <= 1 else None
                for v in cast(list[object], raw)
            ]
        samples = self._samples.setdefault(device_id, deque())
        samples.append(
            _Sample(now, float(cover) if isinstance(cover, int | float) else None, cells)
        )
        while samples and now - samples[0].at > KEEP:
            samples.popleft()

    def _recent(self, device_id: str, now: dt.datetime) -> list[_Sample]:
        return [s for s in self._samples.get(device_id, ()) if now - s.at <= WINDOW]

    def cover(self, device_id: str, now: dt.datetime) -> float | None:
        values = [s.cover for s in self._recent(device_id, now) if s.cover is not None]
        return statistics.median(values) if values else None

    def cells(self, device_id: str, now: dt.datetime) -> list[float | None]:
        recent = [s.cells for s in self._recent(device_id, now) if s.cells]
        out: list[float | None] = []
        for i in range(24 if recent else 0):
            values = [c[i] for c in recent if c[i] is not None]
            out.append(statistics.median(cast(list[float], values)) if values else None)
        return out


@dataclass(frozen=True)
class Source:
    minutes: float  # until these clouds (or this gap) arrive
    cover: float
    label: str


@dataclass(frozen=True)
class Step:
    minutes: int
    cover: float
    label: str


@dataclass(frozen=True)
class Nowcast:
    cover: float  # now, 0..1
    wind_kmh: float
    wind_from: float
    steps: list[Step]
    summary: str

    @property
    def wind_text(self) -> str:
        return f"Wind aus {fireball.heading(self.wind_from)}, {self.wind_kmh:.0f} km/h"


def _wind(hours: list[Hour], now: dt.datetime) -> tuple[float, float] | None:
    for h in hours:
        if h.at <= now < h.at + dt.timedelta(hours=1):
            if h.wind_kmh is None or h.wind_from is None:
                return None
            return h.wind_kmh, h.wind_from
    return None


def _own_sources(cells: list[float | None], wind_from: float, kmh: float) -> list[Source]:
    """The upwind sector of the own cloud map, low and middle band."""
    if not cells:
        return []
    sector = math.floor((wind_from % 360.0 + 22.5) / 45.0) % 8
    out: list[Source] = []
    for band, (low, high) in enumerate(BANDS[:2]):
        value = cells[8 * band + sector]
        if value is None:
            continue
        mid = math.radians((low + high) / 2)
        km = CLOUD_KM / math.tan(mid)
        out.append(Source(60 * km / kmh, value, "am eigenen Himmel"))
    return out


def _neighbour_sources(
    place: tuple[float, float],
    neighbours: list[tuple[tuple[float, float], float]],
    wind_from: float,
    kmh: float,
) -> list[Source]:
    out: list[Source] = []
    for there, cover in neighbours:
        km = fireball.distance_km(*place, *there)
        if km < 1 or km > REACH_KM:
            continue
        off = math.radians(fireball.bearing_deg(*place, *there) - wind_from)
        along, cross = km * math.cos(off), abs(km * math.sin(off))
        if along <= 0 or cross > CROSS_KM:
            continue
        where = fireball.compass(fireball.bearing_deg(*place, *there))
        out.append(Source(60 * along / kmh, cover, f"Kamera {km:.0f} km {where}"))
    return out


def _summary(cover: float, steps: list[Step]) -> str:
    if cover >= CLOUDY:
        gap = next((s for s in steps if s.cover <= CLEAR), None)
        if gap is not None:
            return f"Es klart in etwa {gap.minutes} Minuten auf."
        return "Keine Lücke in Sicht."
    if cover <= CLEAR:
        front = next((s for s in steps if s.cover >= CLOUDY), None)
        if front is not None:
            return f"Wolken in etwa {front.minutes} Minuten."
        return "Bleibt klar."
    return "Wechselhaft."


def forecast(
    history: History,
    device_id: str,
    place: tuple[float, float],
    hours: list[Hour],
    neighbours: list[tuple[tuple[float, float], float]],
    now: dt.datetime,
) -> Nowcast | None:
    """None without a current sky measurement or without wind (by day, in twilight, or
    when the forecast has no wind)."""
    cover = history.cover(device_id, now)
    wind = _wind(hours, now)
    if cover is None or wind is None:
        return None
    kmh, wind_from = wind
    if kmh < MIN_WIND_KMH:
        return Nowcast(cover, kmh, wind_from, [], "Kaum Wind: die Wolken bleiben, wie sie sind.")
    sources = _own_sources(history.cells(device_id, now), wind_from, kmh)
    sources += _neighbour_sources(place, neighbours, wind_from, kmh)
    steps: list[Step] = []
    for minutes in range(STEP_MIN, HORIZON_MIN + 1, STEP_MIN):
        near = [s for s in sources if abs(s.minutes - minutes) <= STEP_MIN / 2]
        if not near:
            continue
        steps.append(
            Step(
                minutes,
                statistics.fmean(s.cover for s in near),
                ", ".join(dict.fromkeys(s.label for s in near)),
            )
        )
    # The own map sees closer than 15 minutes when the wind is fast: those count for "soon".
    soon = [s for s in sources if s.minutes < STEP_MIN / 2]
    if soon:
        value = statistics.fmean(s.cover for s in soon)
        steps.insert(0, Step(max(1, round(statistics.fmean(s.minutes for s in soon))), value,
                             ", ".join(dict.fromkeys(s.label for s in soon))))  # fmt: skip
    return Nowcast(cover, kmh, wind_from, steps, _summary(cover, steps))


# Filled by the device API from every status with a sky measurement.
HISTORY = History()


def _place(device: Device) -> tuple[float, float] | None:
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return float(lat), float(lon)


async def for_device(
    db: AsyncSession, cache: ForecastCache, device: Device, now: dt.datetime | None = None
) -> Nowcast | None:
    """The nowcast for a camera; its neighbours only when it is in the network itself."""
    now = now or dt.datetime.now(dt.UTC)
    place = _place(device)
    if place is None or HISTORY.cover(device.id, now) is None:
        return None
    neighbours: list[tuple[tuple[float, float], float]] = []
    if device.network:
        others = await db.scalars(
            select(Device).where(Device.network.is_(True), Device.id != device.id)
        )
        for other in others:
            there, cover = _place(other), HISTORY.cover(other.id, now)
            if there is not None and cover is not None:
                neighbours.append((there, cover))
    hours = await cache.get(*place)
    return forecast(HISTORY, device.id, place, hours, neighbours, now)
