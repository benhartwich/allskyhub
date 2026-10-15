"""Queries and file answers for a camera's detections (SPEC §6.4)."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, cast

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_protocol import EventVariant
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import EventRecord


def trail(event: EventRecord) -> str:
    """SPEC §6.4 Trail verdicts: the camera's verdict; missing means ``meteor``."""
    value = (event.data or {}).get("trail")
    return value if isinstance(value, str) and value else "meteor"


def has_frame(event: EventRecord) -> bool:
    """SPEC §6.4 Original frame: the event names the frame it was found in."""
    frame = (event.data or {}).get("frame")
    return event.has_image and isinstance(frame, str) and bool(frame)


def crop(event: EventRecord) -> tuple[int, int, int, int] | None:
    """The picture's window in the original frame (``crop_x/y/w/h``), None for the whole
    frame or when the values make no sense for the frame's size."""
    data = event.data or {}
    values = [data.get(k) for k in ("crop_x", "crop_y", "crop_w", "crop_h")]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
        return None
    x, y, w, h = cast(list[int], values)
    if w == 0 or h == 0:
        return None
    if (
        event.original_w
        and event.original_h
        and (x + w > event.original_w or y + h > event.original_h)
    ):
        return None
    return x, y, w, h


def dismissed(event: EventRecord) -> bool:
    """A false positive: labelled so, or a streak the camera judged not to be a meteor and
    the owner did not confirm. Counts nowhere (SPEC §6.4), also before the camera's
    automatic label has arrived."""
    if event.label == "false_positive":
        return True
    return event.label != "confirmed" and trail(event) != "meteor"


def counted() -> ColumnElement[bool]:
    """``not dismissed(event)`` for queries."""
    return and_(
        or_(EventRecord.label.is_(None), EventRecord.label != "false_positive"),
        or_(
            EventRecord.label == "confirmed",
            ~EventRecord.data.has_key("trail"),
            EventRecord.data["trail"].astext == "meteor",
        ),
    )


# Stored pictures never change.
PRIVATE_CACHE = "private, max-age=86400"


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
    ``hide_false`` leaves out false positives (``dismissed``)."""
    query = select(EventRecord).where(EventRecord.device_id == device_id)
    if hide_false:
        query = query.where(counted())
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
    variant: EventVariant,
) -> Response:
    """An event's picture: 202 ``{"status": "requested"}`` while the hub fetches it from the
    camera, 404 when there is none or the camera is offline. With ``original`` the whole
    frame (SPEC §6.5), for events that name one."""
    event = await db.scalar(
        select(EventRecord).where(
            EventRecord.device_id == device_id,
            EventRecord.night_id == night_id,
            EventRecord.event_id == event_id,
        )
    )
    if event is None or not event.has_image:
        raise HTTPException(404, "Not found")
    if variant is EventVariant.ORIGINAL and not has_frame(event):
        raise HTTPException(404, "Not found")
    path = store.event_path(device_id, night_id, event_id, variant)
    if path.is_file():
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": PRIVATE_CACHE})
    if registry.is_pending(device_id, night_id, event_id, variant, "event") or (
        await registry.request_event(device_id, night_id, event_id, variant)
    ):
        return JSONResponse({"status": "requested"}, status_code=202)
    raise HTTPException(404, "Not available")


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
        "reason": event.label_reason,
        "labelled_at": event.labelled_at.isoformat() if event.labelled_at else None,
    }


# SPEC §6.4 Overflights (roadmap L29): streaks the camera saw move, or that a label names
# as an aircraft or a satellite.
OVERFLIGHT_TRAILS = frozenset({"moving", "circle", "dashed"})
OVERFLIGHT_GROUPS = {"satellite": "Satelliten", "aircraft": "Flugzeuge", "unknown": "Unbekannt"}
# The camera's own verdicts (SPEC §6.4 Trail verdicts) are reasons, not names.
_VERDICT_DETAILS = frozenset({"bewegtes Objekt", "blinkt"})
_POINTS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")


def overflight(event: EventRecord) -> str | None:
    """``satellite``, ``aircraft`` or ``unknown`` for an overflight, else None. A meteor the
    owner confirmed is none."""
    if event.kind != "meteor" or event.label == "confirmed":
        return None
    named = event.label == "false_positive" and event.label_reason in ("satellite", "aircraft")
    if not named and trail(event) not in OVERFLIGHT_TRAILS:
        return None
    return event.label_reason if named and event.label_reason else "unknown"


def overflight_name(event: EventRecord) -> str | None:
    """A satellite's name and NORAD number or an aircraft's call sign, from the label."""
    detail = event.label_detail
    if event.label != "false_positive" or not detail or detail in _VERDICT_DETAILS:
        return None
    return detail


def _point(data: dict[str, Any], end: str) -> str | None:
    az, alt = data.get(f"{end}_azimuth_deg"), data.get(f"{end}_altitude_deg")
    if not isinstance(az, int | float) or not isinstance(alt, int | float):
        return None
    return f"{_POINTS[round(az / 45) % 8]} {round(alt)}°"


def overflight_path(event: EventRecord) -> str | None:
    """ "NW 15° → SO 32°" from the track's ends; None without an orientation."""
    data = event.data or {}
    start, end = _point(data, "start"), _point(data, "end")
    return f"{start} → {end}" if start and end else None


def overflight_count(rows: list[EventRecord]) -> str | None:
    """ "23 Überflüge: 6 Satelliten, 3 Flugzeuge, 14 unbekannt" (SPEC §6.4)."""
    groups = [g for g in (overflight(e) for e in rows) if g is not None]
    if not groups:
        return None
    sats, planes = groups.count("satellite"), groups.count("aircraft")
    unknown = groups.count("unknown")
    parts = [
        f"{sats} {'Satellit' if sats == 1 else 'Satelliten'}" if sats else "",
        f"{planes} {'Flugzeug' if planes == 1 else 'Flugzeuge'}" if planes else "",
        f"{unknown} unbekannt" if unknown else "",
    ]
    total = len(groups)
    head = f"{total} {'Überflug' if total == 1 else 'Überflüge'}"
    return f"{head}: " + ", ".join(p for p in parts if p)
