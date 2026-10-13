"""Opt-in public sky page per camera (roadmap #9).

Shows only what the owner chose to publish (``PublicProfile``): texts about the camera, its
latest image, the last hour as a loop, the night archive with keograms and startrails,
detections, sky quality and the Kp forecast. Coordinates only when the owner allows it, and
then rounded to 0.1°. Never the device id or other status details. Public visitors do not
switch on live view (SPEC §6.5), and never get full-size archive images, which would have to
come from the owner's camera.
"""

from __future__ import annotations

import datetime as dt
import secrets
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_protocol import Observatory, PublicProfile, PublicSections, PublicState
from allskyhub_server.devices import events
from allskyhub_server.models import Device, EventRecord, Frame, Product

# Products shown publicly as images; the night's timelapse comes separately (night_video).
PUBLIC_PRODUCTS = ("keogram.jpg", "startrails.jpg")
# How far back the hub fetches a timelapse by itself for a public page (like Allsky's
# morning upload to its website); older ones only when the owner opens them.
PUBLIC_VIDEO_DAYS = 2


# Detections worth showing to the public; the rest are measurements, not events to look at.
PUBLIC_EVENT_KINDS = ("meteor", "aurora", "nlc", "lightning")
LOOP_WINDOW = dt.timedelta(hours=1)
LOOP_MAX_FRAMES = 90


def profile(device: Device) -> PublicProfile:
    """The stored profile, or the defaults when none (or an outdated one) is stored."""
    try:
        return PublicProfile.model_validate(device.public_profile or {})
    except ValidationError:
        return PublicProfile()


__all__ = ["PublicProfile", "PublicSections"]

# German names of the profile fields for 422 answers (SPEC §6.8).
FIELD_NAMES = {
    "title": "Titel",
    "location_label": "Ort",
    "show_coordinates": "Koordinaten zeigen",
    "camera": "Kamera",
    "lens": "Objektiv",
    "computer": "Rechner",
    "owner": "Betrieben von",
    "notes": "Notizen",
    "sections": "Abschnitte",
    "enabled": "Öffentliche Seite",
    "on_home": "Startseite",
}


def describe_error(exc: ValidationError) -> str:
    """One German sentence for the local UI, which shows it as it is (SPEC §6.8)."""
    parts: list[str] = []
    for err in exc.errors():
        loc = [str(x) for x in err["loc"] if str(x) not in ("profile", "body")]
        name = FIELD_NAMES.get(loc[0], loc[0]) if loc else "Eingabe"
        if len(loc) > 1 and loc[0] == "sections":
            name = f"Abschnitt {loc[1]}"
        kind = err["type"]
        if kind == "string_too_long":
            limit = (err.get("ctx") or {}).get("max_length")
            parts.append(f"{name} ist zu lang (höchstens {limit} Zeichen)")
        elif kind == "extra_forbidden":
            parts.append(f"{name} gibt es nicht")
        else:
            parts.append(f"{name} ist ungültig")
    return "; ".join(dict.fromkeys(parts)) + "."


def state(device: Device, base_url: str) -> PublicState:
    url = f"{base_url.rstrip('/')}/sky/{device.public_slug}" if device.public_slug else None
    return PublicState(enabled=device.public_slug is not None, url=url, profile=profile(device))


def title(device: Device) -> str:
    return profile(device).title or device.name or "Allsky-Kamera"


def coordinates(device: Device) -> tuple[float, float] | None:
    """Rounded to 0.1° (about 10 km), only when the owner allows it and the camera knows them."""
    if not profile(device).show_coordinates:
        return None
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return round(float(lat), 1), round(float(lon), 1)


def coarse_status(device: Device) -> dict[str, Any] | None:
    """The status for what a public page computes from the place ("Heute Nacht": sunset,
    satellite passes): rounded to 0.5° (about 50 km) unless the owner shows the
    coordinates, so minute-exact times do not give the place away."""
    status = device.last_status
    if status is None or profile(device).show_coordinates:
        return status
    settings: dict[str, Any] = dict(status.get("settings") or {})
    for key in ("latitude", "longitude"):
        value = settings.get(key)
        if isinstance(value, int | float):
            settings[key] = round(float(value) * 2) / 2
    return {**status, "settings": settings}


def latitude(device: Device) -> float | None:
    """For the Kp rule of thumb only; never shown unless ``coordinates`` allows it."""
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat = settings.get("latitude")
    return float(lat) if isinstance(lat, int | float) else None


async def loop_frames(db: AsyncSession, device: Device, now: dt.datetime) -> list[Frame]:
    """Thumbnails of the last hour, oldest first, for the loop."""
    rows = await db.scalars(
        select(Frame)
        .where(
            Frame.device_id == device.id,
            Frame.has_thumb.is_(True),
            Frame.captured_at >= now - LOOP_WINDOW,
        )
        .order_by(Frame.captured_at.desc())
        .limit(LOOP_MAX_FRAMES)
    )
    return list(reversed(list(rows)))


async def frame(db: AsyncSession, device: Device, night_id: str, name: str) -> Frame | None:
    return await db.scalar(
        select(Frame).where(
            Frame.device_id == device.id,
            Frame.night_id == night_id,
            Frame.name == name,
            Frame.has_thumb.is_(True),
        )
    )


async def recent_events(
    db: AsyncSession, device: Device, *, night_id: str | None = None, limit: int = 24
) -> list[EventRecord]:
    """Detections with a picture, newest first, without those the owner marked as false."""
    query = select(EventRecord).where(
        EventRecord.device_id == device.id,
        EventRecord.kind.in_(PUBLIC_EVENT_KINDS),
        EventRecord.has_thumb.is_(True),
        events.counted(),
    )
    if night_id is not None:
        query = query.where(EventRecord.night_id == night_id)
    return list(await db.scalars(query.order_by(EventRecord.start.desc()).limit(limit)))


async def event(
    db: AsyncSession, device: Device, night_id: str, event_id: str
) -> EventRecord | None:
    for e in await recent_events(db, device, night_id=night_id, limit=1000):
        if e.event_id == event_id:
            return e
    return None


async def night_products(db: AsyncSession, device: Device, night_id: str) -> list[Product]:
    rows = await db.scalars(
        select(Product)
        .where(
            Product.device_id == device.id,
            Product.night_id == night_id,
            Product.name.in_(PUBLIC_PRODUCTS),
            Product.has_thumb.is_(True),
        )
        .order_by(Product.name)
    )
    return list(rows)


def enable(device: Device) -> str:
    """Give the device a public page (keeps an existing slug). The caller commits."""
    if device.public_slug is None:
        device.public_slug = secrets.token_urlsafe(9)  # 12 characters, 72 random bits
    return device.public_slug


def disable(device: Device) -> None:
    """The caller commits. A later ``enable`` gets a new address."""
    device.public_slug = None


async def by_slug(db: AsyncSession, slug: str) -> Device | None:
    """A paired device with that public page, or None."""
    if not slug or len(slug) > 32:
        return None
    return await db.scalar(
        select(Device).where(Device.public_slug == slug, Device.owner_id.is_not(None))
    )


async def newest_products(db: AsyncSession, device: Device) -> tuple[str | None, list[Product]]:
    """The newest night with a public product the hub holds, and those products."""
    night_id = await db.scalar(
        select(Product.night_id)
        .where(
            Product.device_id == device.id,
            Product.name.in_(PUBLIC_PRODUCTS),
            Product.has_thumb.is_(True),
        )
        .order_by(Product.night_id.desc())
        .limit(1)
    )
    if night_id is None:
        return None, []
    rows = await db.scalars(
        select(Product)
        .where(
            Product.device_id == device.id,
            Product.night_id == night_id,
            Product.name.in_(PUBLIC_PRODUCTS),
            Product.has_thumb.is_(True),
        )
        .order_by(Product.name)
    )
    return night_id, list(rows)


def shows_videos(device: Device) -> bool:
    """The public page shows the nights' timelapses (with its archive section)."""
    return device.public_slug is not None and profile(device).sections.archive


async def night_video(db: AsyncSession, device: Device, night_id: str) -> Product | None:
    """The night's timelapse, if the hub holds the full file (it is never fetched for an
    anonymous visitor; see ``PUBLIC_VIDEO_DAYS``)."""
    if not shows_videos(device):
        return None
    return await db.scalar(
        select(Product).where(
            Product.device_id == device.id,
            Product.night_id == night_id,
            Product.kind == "timelapse",
            Product.has_full.is_(True),
        )
    )


def observatory(device: Device) -> Observatory | None:
    """SPEC §6.3 ``status.observatory`` (roadmap C15), parsed, or None."""
    raw = (device.last_status or {}).get("observatory")
    if not isinstance(raw, dict):
        return None
    try:
        return Observatory.model_validate(raw)
    except ValidationError:
        return None
