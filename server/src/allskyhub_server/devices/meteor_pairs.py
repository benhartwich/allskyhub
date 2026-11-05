"""Meteors seen by two cameras (roadmap C4): pairing and trajectory.

Two meteor events pair when their exposures overlap (SPEC §6.4: the meteor happened
somewhere within ``start``..``end``), both carry their track ends and the triangulation
gives a plausible trajectory. Only cameras whose data may meet are paired: cameras of
the same owner, or two cameras whose owners both joined the network (``Device.network``).
A pair of other owners' cameras tells each owner much about where the other camera stands
(and a dishonest owner could send made-up meteors to find out), so it takes both owners'
consent; showing rounded coordinates on a public page is not that.

Three cameras or more give one pair per two of them; every pair of the meteor then keeps
the solution of all of them together (``triangulation.solve``) with its error, as long as
each of these cameras may pair with each other one.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import events, fireball, triangulation
from allskyhub_server.models import Device, EventRecord, MeteorPair

log = logging.getLogger(__name__)

# Exposure boundaries are known to about a second (both clocks synchronised).
SLACK = dt.timedelta(seconds=1)
_ENDS = ("start_azimuth_deg", "start_altitude_deg", "end_azimuth_deg", "end_altitude_deg")


def sighting(device: Device, event: EventRecord) -> triangulation.Sighting | None:
    data: dict[str, Any] = event.data or {}
    if data.get("trial"):  # a station's meteor found by the hub, before its trial (§10.7)
        return None
    values = [data.get(k) for k in _ENDS]
    if not all(isinstance(v, int | float) and math.isfinite(v) for v in values):
        return None
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    elevation = settings.get("elevation_m")
    height = float(elevation) / 1000 if isinstance(elevation, int | float) else 0.0
    az1, alt1, az2, alt2 = (float(v) for v in values)  # pyright: ignore[reportArgumentType]
    # The plate solve's error (SPEC §6.3 status `orientation`) is how far its directions go.
    orientation: dict[str, Any] = (device.last_status or {}).get("orientation") or {}
    rms = orientation.get("rms_deg")
    sigma = triangulation.DEFAULT_SIGMA_DEG
    if isinstance(rms, int | float) and math.isfinite(rms):  # JSON lets NaN through
        sigma = min(float(rms), triangulation.MAX_SIGMA_DEG)
    return triangulation.Sighting(
        float(lat), float(lon), height, (az1, alt1), (az2, alt2), sigma_deg=sigma
    )


def may_pair(a: Device, b: Device) -> bool:
    if a.owner_id is None or b.owner_id is None:
        return False
    if a.owner_id == b.owner_id:
        return True
    return a.network and b.network


async def pair_event(
    maker: async_sessionmaker[AsyncSession],
    device_id: str,
    event_id: str,
    on_fireball: Callable[[uuid.UUID], Awaitable[None]] | None = None,
) -> int:
    """Look for the same meteor at other cameras; returns how many pairs were stored.
    ``on_fireball`` gets each stored pair that may have dropped meteorites, after commit."""
    stored = 0
    fireballs: list[uuid.UUID] = []
    found: list[tuple[Device, EventRecord, triangulation.Sighting]] = []
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
                        events.counted(),
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
                pair_id = await _store(db, (device, event), (other, other_event), trajectory)
                stored += 1
                found.append((other, other_event, theirs))
                fireballs.append(pair_id)
            if found:
                await _solve_together(db, [(device, event, mine), *found])
            await db.commit()
            # The joint solution may have moved the end below the line or above it.
            fireballs = [
                pair_id
                for pair_id in fireballs
                # Fresh from the database: the session still holds the pairwise values.
                if (pair := await db.get(MeteorPair, pair_id, populate_existing=True)) is not None
                and pair.low_km <= fireball.METEORITE_END_KM
            ]
        if on_fireball is not None:
            for pair_id in fireballs:
                await on_fireball(pair_id)
    except Exception:
        log.exception("meteor pairing failed")
    return stored


Member = tuple[Device, EventRecord, triangulation.Sighting]


def _key(member: Member) -> tuple[str, str]:
    return member[0].id, member[1].event_id


async def _solve_together(db: AsyncSession, members: list[Member]) -> None:
    """Give every pair of this meteor the solution of all its cameras: the ones just
    paired and the ones their events were paired with before."""
    group: list[Member] = []

    def join(member: Member) -> None:
        if _key(member) not in {_key(m) for m in group} and all(
            may_pair(member[0], m[0]) for m in group
        ):
            group.append(member)

    for member in members:
        join(member)
    keys = [_key(m) for m in group]
    earlier = await db.scalars(
        select(MeteorPair).where(
            or_(
                tuple_(MeteorPair.device_a, MeteorPair.event_a).in_(keys),
                tuple_(MeteorPair.device_b, MeteorPair.event_b).in_(keys),
            )
        )
    )
    for pair in earlier.all():
        for device_id, event_id in ((pair.device_a, pair.event_a), (pair.device_b, pair.event_b)):
            if (device_id, event_id) in {_key(m) for m in group}:
                continue
            other = await db.get(Device, device_id)
            other_event = await db.scalar(
                select(EventRecord).where(
                    EventRecord.device_id == device_id, EventRecord.event_id == event_id
                )
            )
            seen = sighting(other, other_event) if other and other_event else None
            if other is not None and other_event is not None and seen is not None:
                join((other, other_event, seen))
    keys = sorted(_key(m) for m in group)
    solution = triangulation.solve(
        [m[2] for m in sorted(group, key=_key)], seed="|".join(f"{d}/{e}" for d, e in keys)
    )
    if solution is None:
        return
    high, low = solution.trajectory.points
    peaks = [p for p in (_peak(m[1]) for m in group) if p is not None]
    values = {
        "high_lat": high.lat, "high_lon": high.lon, "high_km": high.height_km,
        "low_lat": low.lat, "low_lon": low.lon, "low_km": low.height_km,
        "length_km": solution.trajectory.length_km,
        "convergence_deg": solution.trajectory.convergence_deg,
        "peak": max(peaks) if peaks else None,
        "cameras": sorted({d for d, _ in keys}),
        "high_err_km": solution.high_err_km, "low_err_km": solution.low_err_km,
    }  # fmt: skip
    await db.execute(
        update(MeteorPair)
        .where(
            tuple_(MeteorPair.device_a, MeteorPair.event_a).in_(keys),
            tuple_(MeteorPair.device_b, MeteorPair.event_b).in_(keys),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )


def _peak(event: EventRecord) -> float | None:
    peak = (event.data or {}).get("peak")
    return float(peak) if isinstance(peak, int | float) else None


async def _store(
    db: AsyncSession,
    first: tuple[Device, EventRecord],
    second: tuple[Device, EventRecord],
    trajectory: triangulation.Trajectory,
) -> uuid.UUID:
    (da, ea), (db_, eb) = sorted((first, second), key=lambda pair: pair[0].id)
    high, low = trajectory.points
    peaks = [p for p in (_peak(ea), _peak(eb)) if p is not None]
    values = {
        "at": max(ea.start, eb.start),
        "high_lat": high.lat, "high_lon": high.lon, "high_km": high.height_km,
        "low_lat": low.lat, "low_lon": low.lon, "low_km": low.height_km,
        "length_km": trajectory.length_km, "convergence_deg": trajectory.convergence_deg,
        "peak": max(peaks) if peaks else None,
    }  # fmt: skip
    result = await db.execute(
        pg_insert(MeteorPair)
        .values(device_a=da.id, event_a=ea.event_id, device_b=db_.id, event_b=eb.event_id, **values)
        .on_conflict_do_update(
            index_elements=["device_a", "event_a", "device_b", "event_b"], set_=values
        )
        .returning(MeteorPair.id)
    )
    return result.scalar_one()


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
    device = await db.get(Device, device_id)
    out: list[tuple[MeteorPair, Device]] = []
    for pair in rows:
        other_id = pair.device_b if pair.device_a == device_id else pair.device_a
        other = await db.get(Device, other_id)
        # An owner who left the network takes their pairs along.
        if device is not None and other is not None and may_pair(device, other):
            out.append((pair, other))
    return out
