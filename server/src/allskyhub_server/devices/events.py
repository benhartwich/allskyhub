"""Queries and file answers for a camera's detections (SPEC §6.4)."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

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


LABELS = ("confirmed", "false_positive")
STORM_PATTERN = r"^storm-\d{8}T\d{6}Z$"


@dataclass
class Storm:
    """All lightning flashes with the same ``data.storm`` key (SPEC §6.4), shown as one."""

    key: str
    flashes: list[EventRecord] = field(default_factory=list[EventRecord])

    is_storm = True

    @property
    def start(self) -> dt.datetime:
        return min(e.start for e in self.flashes)

    @property
    def end(self) -> dt.datetime:
        return max(e.end for e in self.flashes)

    @property
    def night_id(self) -> str:
        return self.flashes[0].night_id

    @property
    def cover(self) -> EventRecord:
        """The flash that lit up the largest part of the sky."""

        def area(e: EventRecord) -> float:
            value = e.data.get("area_frac")
            return float(value) if isinstance(value, int | float) else 0.0

        return max(self.flashes, key=area)


def image_rev(data: dict[str, Any]) -> int:
    """SPEC §6.4: revision of a replaceable picture (1 if the device does not say)."""
    rev = data.get("image_rev")
    return rev if isinstance(rev, int) and not isinstance(rev, bool) else 1


def storm_key(event: EventRecord) -> str | None:
    key = event.data.get("storm") if event.kind == "lightning" else None
    return key if isinstance(key, str) and re.fullmatch(STORM_PATTERN, key) else None


def collapse(rows: list[EventRecord]) -> list[EventRecord | Storm]:
    """Lightning flashes of one storm become one entry, at the place of its newest flash."""
    storms: dict[str, Storm] = {}
    items: list[EventRecord | Storm] = []
    for event in rows:
        key = storm_key(event)
        if key is None:
            items.append(event)
            continue
        storm = storms.get(key)
        if storm is None:
            storm = storms[key] = Storm(key)
            items.append(storm)
        storm.flashes.append(event)
    return items


async def storm_flashes(db: AsyncSession, device_id: str, key: str) -> list[EventRecord]:
    rows = await db.scalars(
        select(EventRecord)
        .where(
            EventRecord.device_id == device_id,
            EventRecord.kind == "lightning",
            EventRecord.data["storm"].astext == key,
        )
        .order_by(EventRecord.start)
    )
    return list(rows)


async def events(
    db: AsyncSession,
    device_id: str,
    *,
    night_id: str | None = None,
    before: dt.datetime | None = None,
    limit: int = 50,
    hide_false: bool = False,
) -> list[EventRecord]:
    """Events newest first; one night, or across nights with paging by ``before``.
    ``hide_false`` leaves out those the owner marked as false positives."""
    query = select(EventRecord).where(EventRecord.device_id == device_id)
    if hide_false:
        query = query.where((EventRecord.label.is_(None)) | (EventRecord.label != "false_positive"))
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


async def set_label(
    db: AsyncSession, device_id: str, night_id: str, event_id: str, label: str | None
) -> EventRecord:
    """The owner's verdict on an event (or None to clear it). The caller commits."""
    if label is not None and label not in LABELS:
        raise HTTPException(400, "Unknown label")
    event = await db.scalar(
        select(EventRecord).where(
            EventRecord.device_id == device_id,
            EventRecord.night_id == night_id,
            EventRecord.event_id == event_id,
        )
    )
    if event is None:
        raise HTTPException(404, "Not found")
    event.label = label
    event.labelled_at = dt.datetime.now(dt.UTC) if label else None
    await db.flush()
    return event


def export_row(event: EventRecord) -> dict[str, object]:
    """One event for tuning the detector: what the camera said and what the owner said."""
    return {
        "id": event.event_id,
        "night_id": event.night_id,
        "kind": event.kind,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "confidence": event.confidence,
        "data": event.data,
        "label": event.label,
        "labelled_at": event.labelled_at.isoformat() if event.labelled_at else None,
    }
