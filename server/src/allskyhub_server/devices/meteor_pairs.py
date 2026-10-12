"""Meteors seen by two cameras (roadmap C4): pairing and trajectory.

Two meteor events pair when their exposures overlap (SPEC §6.4: the meteor happened
somewhere within ``start``..``end``), both carry their track ends and the triangulation
gives a plausible trajectory. Only cameras whose data may meet are paired: cameras of
the same owner, or two cameras whose owners show their location on a public page.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import public, triangulation
from allskyhub_server.models import Device, EventRecord, MeteorPair

log = logging.getLogger(__name__)

# Exposure boundaries are known to about a second (both clocks synchronised).
SLACK = dt.timedelta(seconds=1)
_ENDS = ("start_azimuth_deg", "start_altitude_deg", "end_azimuth_deg", "end_altitude_deg")


def sighting(device: Device, event: EventRecord) -> triangulation.Sighting | None:
    data: dict[str, Any] = event.data or {}
    values = [data.get(k) for k in _ENDS]
    if not all(isinstance(v, int | float) for v in values):
        return None
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    elevation = settings.get("elevation_m")
    height = float(elevation) / 1000 if isinstance(elevation, int | float) else 0.0
    az1, alt1, az2, alt2 = (float(v) for v in values)  # pyright: ignore[reportArgumentType]
    return triangulation.Sighting(float(lat), float(lon), height, (az1, alt1), (az2, alt2))


def may_pair(a: Device, b: Device) -> bool:
    if a.owner_id is None or b.owner_id is None:
        return False
    if a.owner_id == b.owner_id:
        return True
    return public.coordinates(a) is not None and public.coordinates(b) is not None


async def pair_event(maker: async_sessionmaker[AsyncSession], device_id: str, event_id: str) -> int:
    """Look for the same meteor at other cameras; returns how many pairs were stored."""
    stored = 0
    try:
        async with maker() as db:
            device = await db.get(Device, device_id)
            event = await db.scalar(
                select(EventRecord).where(
                    EventRecord.device_id == device_id, EventRecord.event_id == event_id
                )
            )
            if device is None or event is None or event.kind != "meteor":
                return 0
            mine = sighting(device, event)
            if mine is None:
                return 0
            candidates = (
                await db.execute(
                    select(EventRecord, Device)
                    .join(Device, Device.id == EventRecord.device_id)
                    .where(
                        EventRecord.kind == "meteor",
                        EventRecord.device_id != device_id,
                        EventRecord.start <= event.end + SLACK,
                        EventRecord.end >= event.start - SLACK,
                        or_(EventRecord.label.is_(None), EventRecord.label != "false_positive"),
                    )
                )
            ).all()
            for other_event, other in candidates:
                if not may_pair(device, other):
                    continue
                theirs = sighting(other, other_event)
                if theirs is None:
                    continue
                trajectory = triangulation.triangulate(mine, theirs)
                if trajectory is None:
                    continue
                stored += await _store(db, (device, event), (other, other_event), trajectory)
            await db.commit()
    except Exception:
        log.exception("meteor pairing failed")
    return stored


async def _store(
    db: AsyncSession,
    first: tuple[Device, EventRecord],
    second: tuple[Device, EventRecord],
    trajectory: triangulation.Trajectory,
) -> int:
    (da, ea), (db_, eb) = sorted((first, second), key=lambda pair: pair[0].id)
    high, low = trajectory.points
    values = {
        "at": max(ea.start, eb.start),
        "high_lat": high.lat, "high_lon": high.lon, "high_km": high.height_km,
        "low_lat": low.lat, "low_lon": low.lon, "low_km": low.height_km,
        "length_km": trajectory.length_km, "convergence_deg": trajectory.convergence_deg,
    }  # fmt: skip
    await db.execute(
        pg_insert(MeteorPair)
        .values(device_a=da.id, event_a=ea.event_id, device_b=db_.id, event_b=eb.event_id, **values)
        .on_conflict_do_update(
            index_elements=["device_a", "event_a", "device_b", "event_b"], set_=values
        )
    )
    return 1


async def for_event(
    db: AsyncSession, device_id: str, event_id: str
) -> list[tuple[MeteorPair, Device]]:
    """The pairs of one event, each with the other camera."""
    rows = await db.scalars(
        select(MeteorPair).where(
            or_(
                (MeteorPair.device_a == device_id) & (MeteorPair.event_a == event_id),
                (MeteorPair.device_b == device_id) & (MeteorPair.event_b == event_id),
            )
        )
    )
    out: list[tuple[MeteorPair, Device]] = []
    for pair in rows:
        other_id = pair.device_b if pair.device_a == device_id else pair.device_a
        other = await db.get(Device, other_id)
        if other is not None:
            out.append((pair, other))
    return out
