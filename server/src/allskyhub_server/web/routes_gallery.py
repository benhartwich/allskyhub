"""Gallery in the web UI (roadmap #13): nights, frames and night products, like in the app."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import Response

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices import archive, queries
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.models import Device
from allskyhub_server.web.deps import CurrentSession, DbSession, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])

NightId = Annotated[str, Path(pattern=archive.NIGHT_ID)]
ProductName = Annotated[str, Path(pattern=archive.PRODUCT_NAME)]
PRODUCT_TITLES = {"keogram": "Keogramm", "startrails": "Sternspuren", "timelapse": "Zeitraffer"}


async def _own(db: DbSession, session: CurrentSession, device_id: str) -> Device:
    device = await queries.owned_one(db, session.user.id, device_id)
    if device is None:
        raise HTTPException(404)
    return device


def _registry(request: Request) -> ConnectionRegistry:
    registry: ConnectionRegistry = request.app.state.connections
    return registry


@router.get("/cameras/{device_id}/gallery")
async def gallery(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own(db, session, device_id)
    return render(
        request,
        "gallery.html",
        {"device": device, "nights": await archive.nights(db, device.id)},
        session=session,
    )


@router.get("/cameras/{device_id}/nights/{night_id}")
async def night(
    request: Request, db: DbSession, session: CurrentSession, device_id: str, night_id: NightId
) -> Response:
    device = await _own(db, session, device_id)
    return render(
        request,
        "night.html",
        {
            "device": device,
            "night_id": night_id,
            "frames": await archive.night_frames(db, device.id, night_id),
            "products": await archive.night_products(db, device.id, night_id),
            "titles": PRODUCT_TITLES,
        },
        session=session,
    )


@router.get("/cameras/{device_id}/frames/{night_id}/{name}/{variant}.jpg")
async def frame_image(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night_id: NightId,
    name: Annotated[str, Path(pattern=archive.FRAME_NAME)],
    variant: FrameVariant,
) -> Response:
    device = await _own(db, session, device_id)
    return archive.frame_file(request.app.state.images, device.id, night_id, name, variant)


@router.get("/cameras/{device_id}/products/{night_id}/{name}/file", response_model=None)
async def product_file(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night_id: NightId,
    name: ProductName,
    variant: FrameVariant = FrameVariant.FULL,
) -> Response:
    device = await _own(db, session, device_id)
    return await archive.product_file(
        db, request.app.state.images, _registry(request), device.id, night_id, name, variant
    )


@router.get("/cameras/{device_id}/products/{night_id}/{name}")
async def product_page(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night_id: NightId,
    name: ProductName,
) -> Response:
    """Viewer page; when the hub does not have the file yet it asks the camera and the page
    polls ``…/state`` until it is there."""
    device = await _own(db, session, device_id)
    return render(
        request,
        "product.html",
        {**await _product_state(request, db, device, night_id, name), "titles": PRODUCT_TITLES},
        session=session,
    )


@router.get("/cameras/{device_id}/products/{night_id}/{name}/state")
async def product_state(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night_id: NightId,
    name: ProductName,
) -> Response:
    device = await _own(db, session, device_id)
    return render(
        request,
        "_product_view.html",
        await _product_state(request, db, device, night_id, name),
        session=session,
    )


async def _product_state(
    request: Request, db: DbSession, device: Device, night_id: str, name: str
) -> dict[str, object]:
    product = next(
        (p for p in await archive.night_products(db, device.id, night_id) if p.name == name), None
    )
    if product is None:
        raise HTTPException(404)
    registry = _registry(request)
    state = "ready"
    if not product.has_full:
        pending = registry.is_pending(device.id, night_id, name, FrameVariant.FULL, "product")
        requested = pending or await registry.request_product(
            device.id, night_id, name, FrameVariant.FULL
        )
        state = "fetching" if requested else "offline"
    return {"device": device, "night_id": night_id, "product": product, "state": state}
