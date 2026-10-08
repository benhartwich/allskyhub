"""Queries and file answers for a camera's archive (frames and night products), shared by the
app API and the web UI."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Frame, Product

NIGHT_ID = r"^\d{8}$"
FRAME_NAME = r"^[A-Za-z0-9._-]{1,128}$"
PRODUCT_NAME = r"^(keogram\.jpg|startrails\.jpg|timelapse\.mp4)$"
# Archived files never change once stored.
PRIVATE_CACHE = "private, max-age=86400"


@dataclass
class NightSummary:
    night_id: str
    frames: int = 0
    first: dt.datetime | None = None
    last: dt.datetime | None = None
    products: int = 0
    kinds: list[str] = field(default_factory=list[str])


async def nights(db: AsyncSession, device_id: str) -> list[NightSummary]:
    """Nights with archived frames or products, newest first."""
    by_night: dict[str, NightSummary] = {}
    rows = await db.execute(
        select(
            Frame.night_id, func.count(), func.min(Frame.captured_at), func.max(Frame.captured_at)
        )
        .where(Frame.device_id == device_id, Frame.has_thumb.is_(True))
        .group_by(Frame.night_id)
    )
    for night_id, count, first, last in rows:
        by_night[night_id] = NightSummary(night_id, count, first, last)
    products = await db.execute(
        select(Product.night_id, Product.kind).where(Product.device_id == device_id)
    )
    for night_id, kind in products:
        night = by_night.setdefault(night_id, NightSummary(night_id))
        night.products += 1
        night.kinds.append(kind)
    return sorted(by_night.values(), key=lambda n: n.night_id, reverse=True)


async def night_frames(db: AsyncSession, device_id: str, night_id: str) -> list[Frame]:
    rows = await db.scalars(
        select(Frame)
        .where(Frame.device_id == device_id, Frame.night_id == night_id, Frame.has_thumb.is_(True))
        .order_by(Frame.captured_at)
    )
    return list(rows)


async def night_products(db: AsyncSession, device_id: str, night_id: str) -> list[Product]:
    rows = await db.scalars(
        select(Product)
        .where(Product.device_id == device_id, Product.night_id == night_id)
        .order_by(Product.kind)
    )
    return list(rows)


def frame_file(
    store: ImageStore, device_id: str, night_id: str, name: str, variant: FrameVariant
) -> Response:
    path = store.frame_path(device_id, night_id, name, variant)
    if not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": PRIVATE_CACHE})


async def product_file(
    db: AsyncSession,
    store: ImageStore,
    registry: ConnectionRegistry,
    device_id: str,
    night_id: str,
    name: str,
    variant: FrameVariant,
) -> Response:
    """The product file (videos with range requests, so players can seek). Without the full
    file the hub asks the camera and answers 202 ``{"status": "requested"}``; 404 when the
    camera is offline."""
    product = await db.scalar(
        select(Product).where(
            Product.device_id == device_id, Product.night_id == night_id, Product.name == name
        )
    )
    if product is None:
        raise HTTPException(404, "Not found")
    path = store.product_path(device_id, night_id, name, variant)
    if path.is_file():
        media = product.content_type if variant is FrameVariant.FULL else "image/jpeg"
        return FileResponse(path, media_type=media, headers={"Cache-Control": PRIVATE_CACHE})
    if variant is FrameVariant.FULL and (
        registry.is_pending(device_id, night_id, name, variant, "product")
        or await registry.request_product(device_id, night_id, name, variant)
    ):
        return JSONResponse({"status": "requested"}, status_code=202)
    raise HTTPException(404, "Not available")
