"""Orientations a camera had before it was moved (SPEC §10.8): a fireball sent later from
before the move is checked against the orientation it then had.

When a new orientation (§6.3, from the camera's own solve or the hub's for a station, §4.8)
puts the sky more than ``SAME_DEG`` from the one before, or is mirrored or sized otherwise,
the one before is kept here with the time it was replaced; so is one a new frame size drops
(§10.2). At most ``MAX_KEPT`` a camera, none older than ``KEEP`` (maintenance.py).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, object_session

from allskyhub_server.devices import station_solve
from allskyhub_server.models import Device, PastOrientation
from allskyhub_solve import Solution

log = logging.getLogger(__name__)

# The same camera: as close as two good solutions of it lie (late_fireballs.MAX_OFF_DEG).
SAME_DEG = 0.5
MAX_KEPT = 20
KEEP = dt.timedelta(days=365)


def moved(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    """Whether ``after`` replaces ``before`` as another pose of the camera."""
    if not before or (after and after.get("solved_at") == before.get("solved_at")):
        return False
    old = station_solve.from_orientation(before)
    if old is None:
        return False
    new = station_solve.from_orientation(after) if after else None
    if new is None or (new.width, new.height) != (old.width, old.height):
        return True
    if (new.flip < 0) != (old.flip < 0):
        return True
    off = station_solve.offset_deg(new, old)
    return off is None or off > SAME_DEG


def keep(
    db: AsyncSession | Session | None,
    device_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    now: dt.datetime,
) -> bool:
    """Keep ``before`` when ``after`` is another pose; True when kept. The caller commits
    (``db`` None: the device's own session, for code that changes it in place)."""
    if db is None or before is None or not moved(before, after):
        return False
    db.add(PastOrientation(device_id=device_id, orientation=before, until=now))
    log.info("camera moved, orientation kept", extra={"device_id": device_id})
    return True


def dropped(device: Device, now: dt.datetime) -> None:
    """The device's orientation goes (a new frame size, §10.2): kept, before it is set to
    None in place."""
    keep(
        object_session(device), device.id, (device.last_status or {}).get("orientation"), None, now
    )


async def purge(db: AsyncSession, now: dt.datetime) -> None:
    """None older than ``KEEP``, at most ``MAX_KEPT`` a camera (maintenance.py)."""
    await db.execute(delete(PastOrientation).where(PastOrientation.until < now - KEEP))
    ranked = (
        select(
            PastOrientation.id,
            func.row_number()
            .over(partition_by=PastOrientation.device_id, order_by=PastOrientation.until.desc())
            .label("n"),
        )
    ).subquery()
    beyond = select(ranked.c.id).where(ranked.c.n > MAX_KEPT)
    await db.execute(delete(PastOrientation).where(PastOrientation.id.in_(beyond)))


async def known(db: AsyncSession, device: Device) -> list[tuple[Solution, dt.datetime | None]]:
    """Every orientation the camera had, the current one first (``until`` None)."""
    out: list[tuple[Solution, dt.datetime | None]] = []
    found = (device.last_status or {}).get("orientation")
    current = station_solve.from_orientation(found) if found else None
    if current is not None:
        out.append((current, None))
    rows = await db.scalars(
        select(PastOrientation)
        .where(PastOrientation.device_id == device.id)
        .order_by(PastOrientation.until.desc())
    )
    for row in rows.all():
        sol = station_solve.from_orientation(row.orientation)
        if sol is not None:
            out.append((sol, row.until))
    return out
