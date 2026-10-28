"""Sightings by eye from the app (stage 5): fireballs and aurora.

A fireball report is matched with the cameras when it is read: detections of meteors
within ``MATCH_WINDOW`` of the time given by cameras within ``MATCH_KM``, the network's
measured tracks (devices/fireball.py) and other people's reports. Only cameras with a
public page that shows its events are named; the others count.

Aurora photos are re-encoded before they are kept (no metadata such as the place stays)
and show only after an admin approved them (Benjamin, 2026-10-28); three flags send an
approved photo back to the admins.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import events, public
from allskyhub_server.models import (
    AuroraReport,
    Device,
    EventRecord,
    FireballReport,
    MeteorPair,
)

MATCH_WINDOW = dt.timedelta(minutes=3)
MATCH_KM = 600.0
OTHERS_WINDOW = dt.timedelta(minutes=5)
OTHERS_KM = 500.0
PHOTO_MAX_UPLOAD = 12_000_000
PHOTO_EDGE = 2048
THUMB_EDGE = 480
FLAGS_TO_REVIEW = 3
BRIGHTNESS = ("star", "venus", "moon", "day")
FIREBALL_DETAILS = ("green", "red", "orange", "blue", "broke", "trail", "sound")
AURORA_DETAILS = ("glow", "rays", "green", "red", "purple")


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _place(device: Device) -> tuple[float, float] | None:
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        return float(lat), float(lon)
    return None


@dataclass(frozen=True)
class CameraMatch:
    at: dt.datetime
    # Only for cameras whose public page shows events; else None (counted, not named).
    title: str | None
    image: str | None


@dataclass(frozen=True)
class FireballMatches:
    cameras: list[CameraMatch]
    tracks: list[uuid.UUID]  # MeteorPair ids: /feuerkugeln/<id>
    others: list[float]  # other people's reports: distance in km


async def match(db: AsyncSession, report: FireballReport) -> FireballMatches:
    here = (report.lat, report.lon)
    lo, hi = report.seen_at - MATCH_WINDOW, report.seen_at + MATCH_WINDOW
    rows = (
        await db.execute(
            select(EventRecord, Device)
            .join(Device, Device.id == EventRecord.device_id)
            .where(
                EventRecord.kind == "meteor",
                EventRecord.start.between(lo, hi),
                events.counted(),
            )
            .order_by(EventRecord.start)
        )
    ).all()
    cameras: list[CameraMatch] = []
    for event, device in rows:
        place = _place(device)
        if place is None or distance_km(here, place) > MATCH_KM:
            continue
        shows = device.public_slug is not None and public.profile(device).sections.events
        image = (
            f"/sky/{device.public_slug}/events/{event.night_id}/{event.event_id}.jpg"
            f"?v={events.image_rev(event.data)}"
            if shows and event.has_thumb
            else None
        )
        cameras.append(CameraMatch(event.start, public.title(device) if shows else None, image))
    tracks = list(await db.scalars(select(MeteorPair.id).where(MeteorPair.at.between(lo, hi))))
    others_rows = await db.scalars(
        select(FireballReport).where(
            FireballReport.id != report.id,
            FireballReport.seen_at.between(
                report.seen_at - OTHERS_WINDOW, report.seen_at + OTHERS_WINDOW
            ),
        )
    )
    others = [
        round(d) for r in others_rows if (d := distance_km(here, (r.lat, r.lon))) <= OTHERS_KM
    ]
    return FireballMatches(cameras, tracks, [float(d) for d in others])


class PhotoError(Exception):
    pass


def photo_path(root: Path, report_id: uuid.UUID, *, thumb: bool = False) -> Path:
    return root / f"{report_id}{'-thumb' if thumb else ''}.jpg"


def store_photo(root: Path, report_id: uuid.UUID, data: bytes) -> None:
    """Re-encode as JPEG, at most ``PHOTO_EDGE`` px, without any metadata, plus a thumbnail.
    Raises PhotoError for anything that is not a picture."""
    if len(data) > PHOTO_MAX_UPLOAD:
        raise PhotoError("too big")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP", "HEIF", "MPO"):
                raise PhotoError("not a photo")
            img.load()
            picture = img.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PhotoError("not a photo") from exc
    root.mkdir(parents=True, exist_ok=True)
    for edge, thumb in ((PHOTO_EDGE, False), (THUMB_EDGE, True)):
        copy = picture.copy()
        copy.thumbnail((edge, edge))
        tmp = photo_path(root, report_id, thumb=thumb).with_suffix(".tmp")
        copy.save(tmp, "JPEG", quality=85, optimize=True)  # no exif, no icc: nothing to leak
        tmp.replace(photo_path(root, report_id, thumb=thumb))


def delete_photo(root: Path, report_id: uuid.UUID) -> None:
    for thumb in (False, True):
        photo_path(root, report_id, thumb=thumb).unlink(missing_ok=True)


async def aurora_near(
    db: AsyncSession, place: tuple[float, float], since: dt.datetime, radius_km: float
) -> list[tuple[AuroraReport, float]]:
    """Aurora reports since ``since`` within ``radius_km``, nearest first."""
    rows = await db.scalars(select(AuroraReport).where(AuroraReport.seen_at >= since))
    found = [(r, d) for r in rows if (d := distance_km(place, (r.lat, r.lon))) <= radius_km]
    return sorted(found, key=lambda x: x[1])
