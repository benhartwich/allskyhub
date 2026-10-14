"""Meteor showers live from the camera network (roadmap N3).

Which showers are active (IMO working list: activity, peak, ZHR, radiant and its daily
drift), how many meteors the network's cameras count per hour of clear sky, and the
radiant measured from meteors two cameras triangulated (devices/meteor_pairs.py): the
direction a meteor came from is the line from the low end of its path back to the high
end, as right ascension and declination. That is the apparent radiant, without zenith
attraction or diurnal aberration, which shift it by a degree or two.

Only cameras whose owners joined the network (``Device.network``) count.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import events, triangulation
from allskyhub_server.devices.event_text import shower_name
from allskyhub_server.models import Device, EventRecord, MeteorPair, SkySample
from allskyhub_sky import sidereal_deg


@dataclass(frozen=True)
class Shower:
    key: str  # the agent's name (SPEC §6.4 `shower`)
    start: tuple[int, int]  # month, day
    end: tuple[int, int]
    peak: tuple[int, int]
    zhr: int
    ra: float  # radiant at the peak, degrees
    dec: float
    dra: float  # daily drift, degrees
    ddec: float

    @property
    def name(self) -> str:
        return shower_name(self.key)


# IMO working list, major showers (the same as the camera's, agent/detect/meteor.py).
SHOWERS = (
    Shower("Quadrantids", (12, 28), (1, 12), (1, 4), 110, 230.0, 49.0, 0.8, -0.2),
    Shower("Lyrids", (4, 16), (4, 25), (4, 22), 18, 271.0, 34.0, 1.1, 0.0),
    Shower("Eta Aquariids", (4, 19), (5, 28), (5, 6), 50, 338.0, -1.0, 0.9, 0.4),
    Shower("Delta Aquariids", (7, 12), (8, 23), (7, 30), 25, 340.0, -16.0, 0.8, 0.18),
    Shower("Perseids", (7, 17), (8, 24), (8, 12), 100, 48.0, 58.0, 1.35, 0.12),
    Shower("Orionids", (10, 2), (11, 7), (10, 21), 20, 95.0, 16.0, 0.7, 0.1),
    Shower("Leonids", (11, 6), (11, 30), (11, 17), 15, 152.0, 22.0, 0.7, -0.4),
    Shower("Geminids", (12, 4), (12, 17), (12, 14), 150, 112.0, 33.0, 1.0, -0.15),
    Shower("Ursids", (12, 17), (12, 26), (12, 22), 10, 217.0, 76.0, 0.0, 0.0),
)
BY_KEY = {s.key: s for s in SHOWERS}
CLEAR_BELOW = 0.3
HOURS = 24
# Measured radiants are collected over this many days.
RADIANT_DAYS = 10


def _in(day: dt.date, start: tuple[int, int], end: tuple[int, int]) -> bool:
    v, a, b = day.month * 100 + day.day, start[0] * 100 + start[1], end[0] * 100 + end[1]
    return a <= v <= b if a <= b else (v >= a or v <= b)


def days_to_peak(shower: Shower, day: dt.date) -> int:
    """Days from ``day`` to the nearest peak (negative: the peak is past)."""
    return min(
        ((dt.date(y, *shower.peak) - day).days for y in (day.year - 1, day.year, day.year + 1)),
        key=abs,
    )


def active(day: dt.date) -> list[Shower]:
    """Showers active on ``day``, strongest first."""
    return sorted((s for s in SHOWERS if _in(day, s.start, s.end)), key=lambda s: -s.zhr)


def upcoming(day: dt.date) -> Shower:
    """The next shower to start after ``day``."""

    def wait(s: Shower) -> int:
        start = dt.date(day.year, *s.start)
        return (start - day).days % 366 or 366

    return min(SHOWERS, key=wait)


def radiant(shower: Shower, day: dt.date) -> tuple[float, float]:
    """(RA, Dec) of the radiant on ``day``, drifting from its peak position."""
    days = -days_to_peak(shower, day)
    return (shower.ra + shower.dra * days) % 360.0, shower.dec + shower.ddec * days


def pair_radiant(pair: MeteorPair) -> tuple[float, float]:
    """The apparent radiant (RA, Dec) of a triangulated meteor: where it came from."""
    high = triangulation.ecef(pair.high_lat, pair.high_lon, pair.high_km)
    low = triangulation.ecef(pair.low_lat, pair.low_lon, pair.low_km)
    x, y, z = (h - lo for h, lo in zip(high, low, strict=True))
    norm = math.sqrt(x * x + y * y + z * z)
    # Earth-fixed to the sky: turn by the Greenwich sidereal time.
    gmst = math.radians(sidereal_deg(pair.at, 0.0))
    xs = x * math.cos(gmst) - y * math.sin(gmst)
    ys = x * math.sin(gmst) + y * math.cos(gmst)
    ra = math.degrees(math.atan2(ys, xs)) % 360.0
    dec = math.degrees(math.asin(max(-1.0, min(1.0, z / norm))))
    return ra, dec


def separation(a: tuple[float, float], b: tuple[float, float]) -> float:
    ra1, d1, ra2, d2 = (math.radians(v) for v in (*a, *b))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def mean_radiant(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    x = y = z = 0.0
    for ra, dec in points:
        r, d = math.radians(ra), math.radians(dec)
        x += math.cos(d) * math.cos(r)
        y += math.cos(d) * math.sin(r)
        z += math.sin(d)
    return math.degrees(math.atan2(y, x)) % 360.0, math.degrees(math.atan2(z, math.hypot(x, y)))


@dataclass
class Hour:
    start: dt.datetime
    meteors: int = 0
    by_shower: dict[str, int] = field(default_factory=dict[str, int])
    clear_cameras: int = 0

    def rate(self, key: str | None = None) -> float | None:
        """Meteors per clear camera in this hour (all, or one shower's)."""
        if not self.clear_cameras:
            return None
        count = self.meteors if key is None else self.by_shower.get(key, 0)
        return count / self.clear_cameras


@dataclass
class Measured:
    pair: MeteorPair
    radiant: tuple[float, float]
    off_deg: float  # from the catalogue radiant


@dataclass
class Live:
    now: dt.datetime
    cameras: int
    hours: list[Hour]
    showers: list[Shower]
    next_shower: Shower
    measured: dict[str, list[Measured]]

    def mean(self, key: str) -> tuple[float, float] | None:
        return mean_radiant([m.radiant for m in self.measured.get(key, [])])

    def total(self, key: str | None = None) -> int:
        return sum(h.meteors if key is None else h.by_shower.get(key, 0) for h in self.hours)


async def live(db: AsyncSession, now: dt.datetime | None = None) -> Live:
    now = now or dt.datetime.now(dt.UTC)
    first = now.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=HOURS - 1)
    network = set(await db.scalars(select(Device.id).where(Device.network.is_(True))))
    hours = [Hour(first + dt.timedelta(hours=i)) for i in range(HOURS)]

    def slot(at: dt.datetime) -> int:
        return int((at - first).total_seconds() // 3600)

    if network:
        meteors = await db.scalars(
            select(EventRecord).where(
                EventRecord.kind == "meteor",
                EventRecord.device_id.in_(network),
                EventRecord.start >= first,
                events.counted(),
            )
        )
        for event in meteors:
            i = slot(event.start)
            if not 0 <= i < HOURS:
                continue
            hours[i].meteors += 1
            data = event.data or {}
            key = data.get("shower")
            if isinstance(key, str) and key and data.get("shower_match") == "radiant":
                hours[i].by_shower[key] = hours[i].by_shower.get(key, 0) + 1
        clear: set[tuple[int, str]] = set()
        samples = await db.execute(
            select(SkySample.device_id, SkySample.at).where(
                SkySample.device_id.in_(network),
                SkySample.at >= first,
                SkySample.cloud_cover < CLEAR_BELOW,
            )
        )
        for device_id, at in samples:
            i = slot(at)
            if 0 <= i < HOURS:
                clear.add((i, device_id))
        for i, _ in clear:
            hours[i].clear_cameras += 1

    today = now.date()
    showers = active(today)
    measured: dict[str, list[Measured]] = {}
    if network and showers:
        pairs = await db.scalars(
            select(MeteorPair).where(
                MeteorPair.at >= now - dt.timedelta(days=RADIANT_DAYS),
                MeteorPair.device_a.in_(network),
                MeteorPair.device_b.in_(network),
            )
        )
        for pair in pairs:
            point = pair_radiant(pair)
            # The nearest active shower's radiant within the camera's tolerance and a bit.
            best = min(
                ((separation(point, radiant(s, pair.at.date())), s) for s in showers),
                key=lambda x: x[0],
            )
            if best[0] <= RADIANT_MATCH_DEG:
                measured.setdefault(best[1].key, []).append(Measured(pair, point, best[0]))
    return Live(now, len(network), hours, showers, upcoming(today), measured)


# A triangulated meteor belongs to a shower when its measured radiant lies this close to
# the catalogue's (the camera uses 6° for the path alone; triangulation is coarser).
RADIANT_MATCH_DEG = 10.0
