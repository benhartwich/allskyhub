"""Public sky pages (/sky/<slug>): no login, only what the owner published (roadmap #9)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import FileResponse, Response

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices import archive, public, sky_chart, space_weather
from allskyhub_server.devices.event_text import EVENT_TITLES, meteor_summary
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.space_weather import KpCache
from allskyhub_server.models import Device
from allskyhub_server.web.deps import DbSession
from allskyhub_server.web.render import render

router = APIRouter()

# Public images change about every five minutes; a short cache keeps load low.
PUBLIC_CACHE = "public, max-age=60"
# Archived thumbnails never change once stored.
ARCHIVE_CACHE = "public, max-age=86400"

NightId = Annotated[str, Path(pattern=archive.NIGHT_ID)]
FrameName = Annotated[str, Path(pattern=archive.FRAME_NAME)]
EventId = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,128}$")]


async def _device(db: DbSession, slug: str) -> Device:
    device = await public.by_slug(db, slug)
    if device is None:
        raise HTTPException(404)
    return device


def _jpeg(path: FsPath, cache: str) -> Response:
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": cache})


def _base(device: Device, slug: str) -> dict[str, object]:
    return {
        "device": device,
        "slug": slug,
        "profile": public.profile(device),
        "title": public.title(device),
        "coordinates": public.coordinates(device),
        "event_titles": EVENT_TITLES,
    }


@router.get("/sky/{slug}")
async def sky_page(request: Request, db: DbSession, slug: str) -> Response:
    device = await _device(db, slug)
    sections = public.profile(device).sections
    now = dt.datetime.now(dt.UTC)
    night_id, products = await public.newest_products(db, device)
    events = await public.recent_events(db, device, limit=12) if sections.events else []
    nights = [n for n in await archive.nights(db, device.id) if n.frames or n.products][:14]
    sky_night = nights[0].night_id if nights else None
    charts = (
        sky_chart.charts(await archive.night_sky(db, device.id, sky_night))
        if sections.sky and sky_night
        else []
    )
    kp = None
    if sections.aurora:
        cache: KpCache = request.app.state.kp
        kp = await cache.get()
    lat = public.latitude(device)
    return render(
        request,
        "public_sky.html",
        {
            **_base(device, slug),
            "night_id": night_id,
            "products": products if sections.archive else [],
            "loop_frames": await public.loop_frames(db, device, now) if sections.loop else [],
            "events": events,
            "nights": nights if sections.archive else [],
            "sky_night": sky_night,
            "sky_charts": charts,
            "kp": kp,
            "today": now.date(),
            "kp_needed": space_weather.needed_kp(lat) if lat is not None else None,
            "base_url": str(request.base_url).rstrip("/"),
        },
    )


@router.get("/sky/{slug}/now")
async def sky_now(request: Request, db: DbSession, slug: str) -> Response:
    """The latest image and its facts; polled by the page."""
    device = await _device(db, slug)
    return render(request, "_public_now.html", {"device": device, "slug": slug})


@router.get("/sky/{slug}/latest.jpg")
async def sky_latest(request: Request, db: DbSession, slug: str) -> Response:
    device = await _device(db, slug)
    store: ImageStore = request.app.state.images
    return _jpeg(store.path(device.id, FrameVariant.FULL), PUBLIC_CACHE)


@router.get("/sky/{slug}/nights/{night_id}")
async def sky_night(request: Request, db: DbSession, slug: str, night_id: NightId) -> Response:
    device = await _device(db, slug)
    sections = public.profile(device).sections
    if not sections.archive:
        raise HTTPException(404)
    events = await public.recent_events(db, device, night_id=night_id, limit=500)
    return render(
        request,
        "public_night.html",
        {
            **_base(device, slug),
            "night_id": night_id,
            "products": await public.night_products(db, device, night_id),
            "frames": await archive.night_frames(db, device.id, night_id),
            "events": events if sections.events else [],
            "meteors": meteor_summary(events) if sections.events else None,
            "sky_charts": (
                sky_chart.charts(await archive.night_sky(db, device.id, night_id))
                if sections.sky
                else []
            ),
        },
    )


@router.get("/sky/{slug}/products/{night_id}/{name}")
async def sky_product(
    request: Request, db: DbSession, slug: str, night_id: str, name: str, full: bool = False
) -> Response:
    device = await _device(db, slug)
    if name not in public.PUBLIC_PRODUCTS or not night_id.isdigit() or len(night_id) != 8:
        raise HTTPException(404)
    store: ImageStore = request.app.state.images
    variant = FrameVariant.FULL if full else FrameVariant.THUMB
    return _jpeg(store.product_path(device.id, night_id, name, variant), PUBLIC_CACHE)


@router.get("/sky/{slug}/frames/{night_id}/{name}")
async def sky_frame(
    request: Request, db: DbSession, slug: str, night_id: NightId, name: FrameName
) -> Response:
    """Thumbnails only (loop and night archive); full images stay private."""
    device = await _device(db, slug)
    sections = public.profile(device).sections
    if (
        not (sections.loop or sections.archive)
        or await public.frame(db, device, night_id, name) is None
    ):
        raise HTTPException(404)
    store: ImageStore = request.app.state.images
    return _jpeg(store.frame_path(device.id, night_id, name, FrameVariant.THUMB), ARCHIVE_CACHE)


@router.get("/sky/{slug}/events/{night_id}/{event_id}.jpg")
async def sky_event_image(
    request: Request, db: DbSession, slug: str, night_id: NightId, event_id: EventId
) -> Response:
    device = await _device(db, slug)
    if not public.profile(device).sections.events:
        raise HTTPException(404)
    if await public.event(db, device, night_id, event_id) is None:
        raise HTTPException(404)
    store: ImageStore = request.app.state.images
    # Replaced pictures get a new image_rev in the URL, so a short cache is enough.
    return _jpeg(store.event_path(device.id, night_id, event_id, FrameVariant.THUMB), PUBLIC_CACHE)
