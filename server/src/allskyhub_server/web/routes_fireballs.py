"""Fireballs seen by two cameras, with their path and possible fall area (devices/fireball.py).

Public when both cameras' owners joined the network (``Device.network``); otherwise only
for those who may see one of the two cameras. Never the cameras' places.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import fireball, map_tiles, public, queries, science
from allskyhub_server.models import Device, MeteorPair
from allskyhub_server.web.deps import DbSession, OptionalSession
from allskyhub_server.web.render import render

router = APIRouter()

LIST_DAYS = 365
LIST_MAX = 200


async def _visible(
    db: AsyncSession, session: SessionInfo | None, pair: MeteorPair
) -> tuple[Device, Device] | None:
    a, b = await db.get(Device, pair.device_a), await db.get(Device, pair.device_b)
    if a is None or b is None:
        return None
    if a.network and b.network:
        return a, b
    if session is None:
        return None
    for device in (a, b):
        if await queries.role_of(db, session.user.id, device) is not None:
            return a, b
    return None


def _camera(device: Device, session: SessionInfo | None) -> dict[str, Any]:
    """How a camera is named: by its public page if it has one, else anonymously."""
    if session is not None and device.owner_id == session.user.id:
        return {"name": device.name, "link": f"/cameras/{device.id}"}
    if device.public_slug:
        return {"name": public.title(device), "link": f"/sky/{device.public_slug}"}
    return {"name": "eine Kamera im Kameranetz", "link": None}


def _item(pair: MeteorPair) -> dict[str, Any]:
    azimuth = fireball.bearing_deg(pair.high_lat, pair.high_lon, pair.low_lat, pair.low_lon)
    return {"pair": pair, "area": fireball.fall_area(pair), "heading": fireball.heading(azimuth)}


def _map_config(pair: MeteorPair, area: fireball.FallArea | None) -> dict[str, Any]:
    return {
        "maxZoom": map_tiles.MAX_ZOOM,
        "layers": {
            key: {"maxZoom": t.max_zoom, "credit": t.credit} for key, t in map_tiles.LAYERS.items()
        },
        "path": [[pair.high_lat, pair.high_lon], [pair.low_lat, pair.low_lon]],
        "heights": [round(pair.high_km), round(pair.low_km)],
        "area": [list(p) for p in area.outline()] if area else None,
    }


@router.get("/feuerkugeln")
async def fireballs(request: Request, db: DbSession, session: OptionalSession) -> Response:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=LIST_DAYS)
    rows = await db.scalars(
        select(MeteorPair)
        .where(
            MeteorPair.at >= since,
            or_(
                MeteorPair.low_km <= fireball.METEORITE_END_KM,
                MeteorPair.peak >= fireball.FIREBALL_PEAK,
            ),
        )
        .order_by(MeteorPair.at.desc())
        .limit(LIST_MAX * 3)
    )
    items: list[dict[str, Any]] = []
    seen: list[dt.datetime] = []
    for pair in rows:
        # Three cameras give three pairs of one meteor: the first one stands for it.
        if any(abs((pair.at - at).total_seconds()) < 30 for at in seen):
            continue
        if await _visible(db, session, pair) is None:
            continue
        seen.append(pair.at)
        items.append(_item(pair))
        if len(items) >= LIST_MAX:
            break
    return render(
        request,
        "fireballs.html",
        {"items": items, "end_km": fireball.METEORITE_END_KM},
        session=session,
    )


@router.get("/feuerkugeln/{pair_id}")
async def fireball_page(
    request: Request, db: DbSession, session: OptionalSession, pair_id: uuid.UUID
) -> Response:
    pair = await db.get(MeteorPair, pair_id)
    cameras = await _visible(db, session, pair) if pair is not None else None
    if pair is None or cameras is None:
        raise HTTPException(404)
    item = _item(pair)
    return render(
        request,
        "fireball.html",
        {
            **item,
            "cameras": [_camera(d, session) for d in cameras],
            "map_config": _map_config(pair, item["area"]),
            "end_km": fireball.METEORITE_END_KM,
            "fireball_form": science.FIREBALL_FORM,
        },
        session=session,
    )
