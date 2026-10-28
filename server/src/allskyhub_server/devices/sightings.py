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

import asyncio
import datetime as dt
import io
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
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
MAX_PIXELS = 50_000_000  # a larger picture is refused before it is decoded
FLAGS_TO_REVIEW = 3
# Distances to other people's reports are only given in steps, so a few queries from
# chosen places cannot pin a reporter down (review of #330).
AURORA_STEP_KM = 25
SWEEP_GRACE_S = 600
OTHERS_STEP_KM = 50
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
    others = sorted(
        step_km(d, OTHERS_STEP_KM)
        for r in others_rows
        if (d := distance_km(here, (r.lat, r.lon))) <= OTHERS_KM
    )
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
            # The app sends JPEG (iPhones' HEIC is converted there).
            if img.format not in ("JPEG", "PNG", "WEBP", "MPO"):
                raise PhotoError("not a photo")
            if img.size[0] * img.size[1] > MAX_PIXELS:
                raise PhotoError("too big")
            img.load()
            # Phones store portrait photos turned, with an EXIF note; turn them upright
            # before the metadata goes.
            picture = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PhotoError("not a photo") from exc
    root.mkdir(parents=True, exist_ok=True)
    for edge, thumb in ((PHOTO_EDGE, False), (THUMB_EDGE, True)):
        copy = picture.copy()
        copy.thumbnail((edge, edge))
        tmp = photo_path(root, report_id, thumb=thumb).with_suffix(".tmp")
        copy.save(tmp, "JPEG", quality=85, optimize=True)  # no exif, no icc: nothing to leak
        tmp.replace(photo_path(root, report_id, thumb=thumb))


def step_km(km: float, step: int) -> int:
    """A distance rounded up to whole steps: 0–25 km → 25 with a step of 25."""
    return max(step, math.ceil(km / step) * step)


async def photo_ids(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """The account's aurora reports with a photo file, to delete them with the account."""
    return list(
        await db.scalars(
            select(AuroraReport.id).where(
                AuroraReport.user_id == user_id, AuroraReport.photo.in_(("pending", "approved"))
            )
        )
    )


async def sweep(db: AsyncSession, root: Path) -> None:
    """Delete photo files whose report is gone (an account deleted with its reports)."""
    kept = {str(i) for i in await db.scalars(select(AuroraReport.id))}
    await asyncio.to_thread(_sweep_files, root, kept)


def _sweep_files(root: Path, kept: set[str], now: float | None = None) -> None:
    if not root.is_dir():
        return
    now = time.time() if now is None else now
    for path in root.glob("*.jpg"):
        if path.name.removesuffix(".jpg").removesuffix("-thumb") in kept:
            continue
        # A photo is written before its report is committed: leave fresh files alone.
        if now - path.stat().st_mtime < SWEEP_GRACE_S:
            continue
        path.unlink(missing_ok=True)


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
