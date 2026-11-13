"""The sky over a camera's saved frames (draft 6, 2026-11-13): for one frame, everything
the live image can show (constellations, star names, grid, moon and planets, satellites)
at the frame's time, drawn with the orientation the camera had then, plus the night's
meteors and overflights near that time.

Orientation: the current one, or the one kept in ``past_orientation`` for a frame taken
before the camera was moved (SPEC §10.8). Only for the camera's own viewers: satellites
would give the exact place away (see web/templating.sky_layers).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import events as event_kinds
from allskyhub_server.devices import sky_layers
from allskyhub_server.devices.satellite_match import Catalogue
from allskyhub_server.devices.sky_overlay import build as constellations
from allskyhub_server.devices.sky_overlay import orientation as usable_orientation
from allskyhub_server.models import Device, EventRecord, PastOrientation
from allskyhub_sky import project

# Meteors and overflights this close to the frame's time are drawn on it.
NEAR = dt.timedelta(minutes=3)


async def orientation_at(
    db: AsyncSession, device: Device, at: dt.datetime
) -> dict[str, Any] | None:
    """The orientation (SPEC §6.3) the camera had at ``at``: the first kept one replaced
    after ``at``, else the current one. Strictly by time, without the two days of slack
    the fireball check allows (past_orientations.SLACK): a frame just after a move must
    get the new orientation."""
    rows = (
        await db.scalars(
            select(PastOrientation)
            .where(PastOrientation.device_id == device.id, PastOrientation.until > at)
            .order_by(PastOrientation.until)
            .limit(1)
        )
    ).all()
    if rows:
        return rows[0].orientation
    found = (device.last_status or {}).get("orientation")
    return cast(dict[str, Any], found) if isinstance(found, dict) else None


@dataclass(frozen=True)
class Track:
    x1: float
    y1: float
    x2: float
    y2: float
    label: str
    kind: str  # "meteor" or "overflight"


def _tracks(rows: list[EventRecord], status: dict[str, Any], at: dt.datetime) -> list[Track]:
    found = usable_orientation(status)
    if found is None or found.lens is None:
        return []
    lens = found.lens
    out: list[Track] = []
    for event in rows:
        overflight = event_kinds.overflight(event)
        if abs(event.start - at) > NEAR or (event_kinds.dismissed(event) and overflight is None):
            continue
        data: dict[str, Any] = event.data or {}
        ends = [data.get(k) for k in ("start_altitude_deg", "start_azimuth_deg",
                                      "end_altitude_deg", "end_azimuth_deg")]  # fmt: skip
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in ends):
            continue
        a1, z1, a2, z2 = (float(cast(float, v)) for v in ends)
        x1, y1 = project(a1, z1, lens, found.north_deg, found.mirrored)
        x2, y2 = project(a2, z2, lens, found.north_deg, found.mirrored)
        if overflight is not None:
            name = event_kinds.overflight_name(event) or overflight
            out.append(Track(x1, y1, x2, y2, name, "overflight"))
        else:
            out.append(Track(x1, y1, x2, y2, "Meteor", "meteor"))
    return out


async def frame_sky(
    db: AsyncSession,
    device: Device,
    at: dt.datetime,
    satellites: Catalogue | None,
    night_events: list[EventRecord],
) -> dict[str, Any] | None:
    """Everything to draw over a frame taken ``at``, as JSON for the page; None without a
    usable lens or place."""
    raw = await orientation_at(db, device, at)
    status: dict[str, Any] = {**(device.last_status or {}), "orientation": raw}
    lines = constellations(status, at)
    layers = sky_layers.build(status, at, satellites)
    if lines is None or layers is None:
        return None

    def marks(items: list[sky_layers.Mark]) -> list[dict[str, Any]]:
        return [
            {"x": round(m.x, 1), "y": round(m.y, 1), "text": m.text, "trail": m.trail}
            for m in items
        ]

    return {
        "width": lines.width,
        "height": lines.height,
        "font": lines.font,
        "lines": " ".join(lines.paths),
        "names": [{"x": round(n.x), "y": round(n.y), "text": n.text} for n in lines.names],
        "cardinals": [{"x": round(c.x), "y": round(c.y), "text": c.text} for c in lines.cardinals],
        "stars": marks(layers.stars),
        "grid": layers.grid,
        "grid_labels": marks(layers.grid_labels),
        "grid_cardinals": marks(layers.cardinals),
        "planets": marks(layers.planets),
        "satellites": marks(layers.satellites),
        "tracks": [
            {
                "x1": round(t.x1),
                "y1": round(t.y1),
                "x2": round(t.x2),
                "y2": round(t.y2),
                "label": t.label,
                "kind": t.kind,
            }
            for t in _tracks(night_events, status, at)
        ],
    }
