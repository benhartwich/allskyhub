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

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.models import Device, EventRecord, Frame, Product

# Products shown publicly: small images only (no timelapse download for anonymous visitors).
PUBLIC_PRODUCTS = ("keogram.jpg", "startrails.jpg")


# Detections worth showing to the public; the rest are measurements, not events to look at.
PUBLIC_EVENT_KINDS = ("meteor", "aurora", "nlc", "lightning")
LOOP_WINDOW = dt.timedelta(hours=1)
LOOP_MAX_FRAMES = 90


class PublicSections(BaseModel):
    """Which parts of the public page are shown."""

    model_config = ConfigDict(extra="forbid")

    live: bool = True
    loop: bool = True
    archive: bool = True
    events: bool = True
    sky: bool = True
    aurora: bool = True


class PublicProfile(BaseModel):
    """What the owner writes about the camera; empty strings are not shown."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(default="", max_length=100)
    location_label: str = Field(default="", max_length=200)
    show_coordinates: bool = False
    camera: str = Field(default="", max_length=200)
    lens: str = Field(default="", max_length=200)
    computer: str = Field(default="", max_length=200)
    owner: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    sections: PublicSections = Field(default_factory=PublicSections)


def profile(device: Device) -> PublicProfile:
    """The stored profile, or the defaults when none (or an outdated one) is stored."""
    try:
        return PublicProfile.model_validate(device.public_profile or {})
    except ValidationError:
        return PublicProfile()


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
        (EventRecord.label.is_(None)) | (EventRecord.label != "false_positive"),
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
