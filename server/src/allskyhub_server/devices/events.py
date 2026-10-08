"""Queries and file answers for a camera's detections (SPEC §6.4)."""

from __future__ import annotations

import datetime as dt

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import EventRecord

# Stored pictures never change.
PRIVATE_CACHE = "private, max-age=86400"


async def events(
    db: AsyncSession,
    device_id: str,
    *,
    night_id: str | None = None,
    before: dt.datetime | None = None,
    limit: int = 50,
) -> list[EventRecord]:
    """Events newest first; one night, or across nights with paging by ``before``."""
    query = select(EventRecord).where(EventRecord.device_id == device_id)
    if night_id is not None:
        query = query.where(EventRecord.night_id == night_id)
    if before is not None:
        query = query.where(EventRecord.start < before)
    rows = await db.scalars(query.order_by(EventRecord.start.desc()).limit(limit))
    return list(rows)


async def event_file(
    db: AsyncSession,
    store: ImageStore,
    registry: ConnectionRegistry,
    device_id: str,
    night_id: str,
    event_id: str,
    variant: FrameVariant,
) -> Response:
    """An event's picture: 202 ``{"status": "requested"}`` while the hub fetches it from the
    camera, 404 when there is none or the camera is offline."""
    event = await db.scalar(
        select(EventRecord).where(
            EventRecord.device_id == device_id,
            EventRecord.night_id == night_id,
            EventRecord.event_id == event_id,
        )
    )
    if event is None or not event.has_image:
        raise HTTPException(404, "Not found")
    path = store.event_path(device_id, night_id, event_id, variant)
    if path.is_file():
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": PRIVATE_CACHE})
    if registry.is_pending(device_id, night_id, event_id, variant, "event") or (
        await registry.request_event(device_id, night_id, event_id, variant)
    ):
        return JSONResponse({"status": "requested"}, status_code=202)
    raise HTTPException(404, "Not available")
