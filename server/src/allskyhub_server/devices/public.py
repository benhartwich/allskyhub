"""Opt-in public sky page per camera (roadmap #9).

Shows only what the owner chose to publish: the camera's name, its latest image, mode and sun
elevation, and the last night's keogram and startrails. Never the location, the device id or
other status details. Public visitors do not switch on live view (SPEC §6.5).
"""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.models import Device, Product

# Products shown publicly: small images only (no timelapse download for anonymous visitors).
PUBLIC_PRODUCTS = ("keogram.jpg", "startrails.jpg")


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
