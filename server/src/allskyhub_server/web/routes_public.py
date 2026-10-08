"""Public sky pages (/sky/<slug>): no login, only what the owner published (roadmap #9)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices import public
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device
from allskyhub_server.web.deps import DbSession
from allskyhub_server.web.render import render

router = APIRouter()

# Public images change about every five minutes; a short cache keeps load low.
PUBLIC_CACHE = "public, max-age=60"


async def _device(db: DbSession, slug: str) -> Device:
    device = await public.by_slug(db, slug)
    if device is None:
        raise HTTPException(404)
    return device


@router.get("/sky/{slug}")
async def sky_page(request: Request, db: DbSession, slug: str) -> Response:
    device = await _device(db, slug)
    night_id, products = await public.newest_products(db, device)
    return render(
        request,
        "public_sky.html",
        {"device": device, "slug": slug, "night_id": night_id, "products": products},
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
    path = store.path(device.id, FrameVariant.FULL)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": PUBLIC_CACHE})


@router.get("/sky/{slug}/products/{night_id}/{name}")
async def sky_product(
    request: Request, db: DbSession, slug: str, night_id: str, name: str, full: bool = False
) -> Response:
    device = await _device(db, slug)
    if name not in public.PUBLIC_PRODUCTS or not night_id.isdigit() or len(night_id) != 8:
        raise HTTPException(404)
    store: ImageStore = request.app.state.images
    variant = FrameVariant.FULL if full else FrameVariant.THUMB
    path = store.product_path(device.id, night_id, name, variant)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": PUBLIC_CACHE})
