"""Orientations a camera had before it was moved (SPEC §10.8): a fireball sent later from
before the move is checked against the orientation it then had.

When a new orientation (§6.3, from the camera's own solve or the hub's for a station, §4.8)
puts the sky more than ``SAME_DEG`` from the one before, or is mirrored or sized otherwise,
the one before is kept here with the time it was replaced; so is one a new frame size drops
(§10.2). The pose kept last only holds longer. At most ``MAX_KEPT`` a camera, none older
than ``KEEP`` (maintenance.py).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import object_session

from allskyhub_server.devices import station_solve
from allskyhub_server.models import Device, PastOrientation
from allskyhub_solve import Solution

log = logging.getLogger(__name__)

# The same pose: two good solutions of one camera lie closer than this. 1° until the spread
# logged since #421 ("station solved again", offset_deg) says better (allsky, 2026-11-08):
# a camera whose solutions scatter by half a degree must not fill MAX_KEPT with noise.
SAME_DEG = 1.0
# A solve trails the move it finds: an orientation counts this long around when it held.
SLACK = dt.timedelta(days=2)
MAX_KEPT = 20
KEEP = dt.timedelta(days=365)

Window = tuple[dt.datetime | None, dt.datetime | None]


def _same(a: Solution, b: Solution) -> bool:
    if (a.width, a.height) != (b.width, b.height) or (a.flip < 0) != (b.flip < 0):
        return False
    off = station_solve.offset_deg(a, b)
    return off is not None and off <= SAME_DEG


def moved(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    """Whether ``after`` replaces ``before`` as another pose of the camera."""
    if not before or (after and after.get("solved_at") == before.get("solved_at")):
        return False
    old = station_solve.from_orientation(before)
    if old is None:
        return False
    new = station_solve.from_orientation(after) if after else None
    return new is None or not _same(new, old)


async def keep(
    db: AsyncSession,
    device_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    now: dt.datetime,
) -> bool:
    """Keep ``before`` when ``after`` is another pose; True when kept. The pose kept last
    (within ``SAME_DEG``) only holds longer; an older one again is a row of its own, so that
    the windows stay in order (A, B, A again: allsky, 2026-11-08). The caller commits."""
    if before is None or not moved(before, after):
        return False
    old = station_solve.from_orientation(before)
    last = await db.scalar(
        select(PastOrientation)
        .where(PastOrientation.device_id == device_id)
        .order_by(PastOrientation.until.desc())
        .limit(1)
    )
    sol = station_solve.from_orientation(last.orientation) if last else None
    if last is not None and old is not None and sol is not None and _same(old, sol):
        last.until = max(last.until, now)
        return True
    db.add(PastOrientation(device_id=device_id, orientation=before, until=now))
    log.info("camera moved, orientation kept", extra={"device_id": device_id})
    return True


def dropped(device: Device, now: dt.datetime) -> None:
    """The device's orientation goes (a new frame size, §10.2): kept, before it is set to
    None in place, in the device's own session (its callers change it synchronously)."""
    before = (device.last_status or {}).get("orientation")
    db = object_session(device)
    if db is not None and before and station_solve.from_orientation(before) is not None:
        db.add(PastOrientation(device_id=device.id, orientation=before, until=now))


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


async def known(db: AsyncSession, device: Device) -> list[tuple[Solution, Window]]:
    """Every orientation the camera had with when it held (from, until; None open), the
    current one first. One held from when the one before was replaced to its own ``until``."""
    rows = (
        await db.scalars(
            select(PastOrientation)
            .where(PastOrientation.device_id == device.id)
            .order_by(PastOrientation.until)
        )
    ).all()
    past: list[tuple[Solution, Window]] = []
    since: dt.datetime | None = None
    for row in rows:
        sol = station_solve.from_orientation(row.orientation)
        if sol is not None:
            past.append((sol, (since, row.until)))
        since = row.until
    found = (device.last_status or {}).get("orientation")
    current = station_solve.from_orientation(found) if found else None
    now: list[tuple[Solution, Window]] = [(current, (since, None))] if current else []
    return now + past[::-1]


def held(window: Window, taken: dt.datetime) -> bool:
    """Whether an orientation that held in ``window`` may be the camera's at ``taken``."""
    since, until = window
    return (since is None or taken >= since - SLACK) and (until is None or taken <= until + SLACK)
