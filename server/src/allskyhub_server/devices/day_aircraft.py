"""Aircraft by day (roadmap H9, SPEC §7.8 By day).

The meteor detector finds overflights only at night; by day the hub lists the aircraft that
crossed a camera's sky from the ADS-B positions alone. Every day round (30 s) each aircraft
at least ``MIN_ALT`` up opens or extends a pass; a pass with no such position for ``GAP``
is closed and kept if it lasted ``MIN_DURATION`` with at least two positions. Open passes
live in memory only: a hub restart loses the ones in the sky at that moment.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.models import AircraftPass, Frame

if TYPE_CHECKING:
    from allskyhub_server.devices.aircraft import Plane, Tracks

log = logging.getLogger(__name__)

MIN_ALT = 20.0
GAP = dt.timedelta(minutes=2)
MIN_DURATION = dt.timedelta(seconds=20)
# The camera's frame for a pass: the hub keeps a thumbnail about every minute (§5.1, §6.5).
FRAME_WITHIN = dt.timedelta(minutes=2)


@dataclass
class _Open:
    device_id: str
    zone: dt.tzinfo
    icao: str
    callsign: str | None
    registration: str | None
    type_code: str | None
    start: dt.datetime
    last: dt.datetime
    start_alt: float
    start_az: float
    end_alt: float
    end_az: float
    top_at: dt.datetime
    top_alt: float
    top_az: float
    alt_m: float | None
    min_km: float
    count: int = 1

    def add(self, plane: Plane, at: dt.datetime) -> None:
        self.last, self.count = at, self.count + 1
        self.end_alt, self.end_az = plane.elevation, plane.azimuth
        self.callsign = plane.flight or self.callsign
        self.min_km = min(self.min_km, plane.distance_km)
        if plane.elevation > self.top_alt:
            self.top_at, self.top_alt, self.top_az = at, plane.elevation, plane.azimuth
            self.alt_m = plane.alt_m

    def record(self) -> AircraftPass:
        return AircraftPass(
            device_id=self.device_id,
            night_id=self.start.astimezone(self.zone).strftime("%Y%m%d"),
            icao=self.icao[:8],
            callsign=(self.callsign or "")[:10] or None,
            registration=self.registration,
            type_code=self.type_code,
            start=self.start,
            end=self.last,
            top_at=self.top_at,
            top_alt_deg=round(self.top_alt, 1),
            top_az_deg=round(self.top_az, 1),
            start_alt_deg=round(self.start_alt, 1),
            start_az_deg=round(self.start_az, 1),
            end_alt_deg=round(self.end_alt, 1),
            end_az_deg=round(self.end_az, 1),
            alt_m=None if self.alt_m is None else round(self.alt_m),
            min_km=round(self.min_km, 1),
        )


class Passes:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker
        self.open: dict[tuple[str, str], _Open] = {}

    async def update(
        self,
        tracks: Tracks,
        cameras: list[tuple[str, float, float, dt.tzinfo]],
        now: dt.datetime,
    ) -> None:
        """One day round: the aircraft up now from each camera; passes that ended are kept."""
        for device_id, lat, lon, zone in cameras:
            for plane in tracks.planes(now, lat, lon, min_elevation=MIN_ALT):
                key = (device_id, plane.hex)
                found = self.open.get(key)
                if found is not None:
                    found.add(plane, now)
                    continue
                registration, type_code = tracks.info.get(plane.hex, (None, None))
                self.open[key] = _Open(
                    device_id, zone, plane.hex, plane.flight, registration, type_code,
                    now, now, plane.elevation, plane.azimuth, plane.elevation, plane.azimuth,
                    now, plane.elevation, plane.azimuth, plane.alt_m, plane.distance_km,
                )  # fmt: skip
        ended = [key for key, p in self.open.items() if now - p.last > GAP]
        done = [self.open.pop(key) for key in ended]
        kept = [p.record() for p in done if p.count >= 2 and p.last - p.start >= MIN_DURATION]
        if kept:
            async with self._maker() as db:
                db.add_all(kept)
                await db.commit()


async def for_night(db: AsyncSession, device_id: str, night_id: str) -> list[AircraftPass]:
    rows = await db.scalars(
        select(AircraftPass)
        .where(AircraftPass.device_id == device_id, AircraftPass.night_id == night_id)
        .order_by(AircraftPass.start)
    )
    return list(rows)


async def count(db: AsyncSession, device_id: str, night_id: str) -> int:
    found = await db.scalar(
        select(func.count())
        .select_from(AircraftPass)
        .where(AircraftPass.device_id == device_id, AircraftPass.night_id == night_id)
    )
    return int(found or 0)


async def one(db: AsyncSession, device_id: str, pass_id: str) -> AircraftPass | None:
    try:
        key = uuid.UUID(pass_id)
    except ValueError:
        return None
    found = await db.get(AircraftPass, key)
    return found if found is not None and found.device_id == device_id else None


async def frame_for(db: AsyncSession, found: AircraftPass) -> Frame | None:
    """The hub's frame nearest the pass's highest point, within ``FRAME_WITHIN``."""
    rows = await db.scalars(
        select(Frame).where(
            Frame.device_id == found.device_id,
            Frame.has_thumb.is_(True),
            Frame.captured_at >= found.top_at - FRAME_WITHIN,
            Frame.captured_at <= found.top_at + FRAME_WITHIN,
        )
    )
    frames = list(rows)
    if not frames:
        return None
    return min(frames, key=lambda f: abs((f.captured_at - found.top_at).total_seconds()))


def adsb_link(found: AircraftPass) -> str:
    """adsb.lol's map of that aircraft, replayed at that time."""
    at = found.top_at.astimezone(dt.UTC)
    return (
        f"https://adsb.lol/?icao={found.icao}&showTrace={at:%Y-%m-%d}"
        f"&timestamp={int(at.timestamp())}"
    )
