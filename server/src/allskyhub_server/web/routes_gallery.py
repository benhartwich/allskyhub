"""Gallery in the web UI (roadmap #13): nights, frames and night products, like in the app."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from allskyhub_protocol import EVENT_ID_PATTERN, FrameVariant
from allskyhub_server.devices import archive, events, queries
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.event_text import EVENT_TITLES, event_rows
from allskyhub_server.models import Device, EventRecord
from allskyhub_server.web.deps import CurrentSession, DbSession, csrf_protect
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])

NightId = Annotated[str, Path(pattern=archive.NIGHT_ID)]
ProductName = Annotated[str, Path(pattern=archive.PRODUCT_NAME)]
EventId = Annotated[str, Path(pattern=EVENT_ID_PATTERN)]
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
            "events": await events.events(
                db, device.id, night_id=night_id, limit=500, hide_false=True
            ),
            "titles": PRODUCT_TITLES,
            "event_titles": EVENT_TITLES,
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


@router.get("/cameras/{device_id}/events")
async def event_list(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
    all: bool = False,
) -> Response:
    """All detections, newest first, 50 per page; false positives only with ``all``."""
    device = await _own(db, session, device_id)
    rows = await events.events(db, device.id, limit=50 * page + 1, hide_false=not all)
    return render(
        request,
        "events.html",
        {
            "device": device,
            "events": rows[50 * (page - 1) : 50 * page],
            "page": page,
            "more": len(rows) > 50 * page,
            "show_all": all,
            "event_titles": EVENT_TITLES,
        },
        session=session,
    )


async def _event(db: DbSession, device: Device, night_id: str, event_id: str) -> EventRecord:
    for event in await events.events(db, device.id, night_id=night_id, limit=1000):
        if event.event_id == event_id:
            return event
    raise HTTPException(404)


async def _event_state(request: Request, device: Device, event: EventRecord) -> str:
    """ready, fetching (the hub asked the camera), offline or none (no picture)."""
    if not event.has_image:
        return "none"
    if event.has_full:
        return "ready"
    registry = _registry(request)
    if registry.is_pending(
        device.id, event.night_id, event.event_id, FrameVariant.FULL, "event"
    ) or await registry.request_event(device.id, event.night_id, event.event_id, FrameVariant.FULL):
        return "fetching"
    return "offline"


@router.get("/cameras/{device_id}/events/{night_id}/{event_id}")
async def event_page(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night_id: NightId,
    event_id: EventId,
) -> Response:
    device = await _own(db, session, device_id)
    event = await _event(db, device, night_id, event_id)
    return render(
        request,
        "event.html",
        {
            "device": device,
            "event": event,
            "state": await _event_state(request, device, event),
            "rows": event_rows(event),
            "event_titles": EVENT_TITLES,
        },
        session=session,
    )


@router.get("/cameras/{device_id}/events/{night_id}/{event_id}/state")
async def event_state(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night_id: NightId,
    event_id: EventId,
) -> Response:
    device = await _own(db, session, device_id)
    event = await _event(db, device, night_id, event_id)
    return render(
        request,
        "_event_view.html",
        {"device": device, "event": event, "state": await _event_state(request, device, event)},
        session=session,
    )


@router.get("/cameras/{device_id}/events/{night_id}/{event_id}/image", response_model=None)
async def event_image(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night_id: NightId,
    event_id: EventId,
    variant: FrameVariant = FrameVariant.FULL,
) -> Response:
    device = await _own(db, session, device_id)
    return await events.event_file(
        db, request.app.state.images, _registry(request), device.id, night_id, event_id, variant
    )


@router.post("/cameras/{device_id}/events/{night_id}/{event_id}/label")
async def label_event(
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night_id: NightId,
    event_id: EventId,
    label: Annotated[str, Form()],
) -> Response:
    """ "Kein Meteor" / "Echter Meteor" / reset; ``label`` "" clears it."""
    device = await _own(db, session, device_id)
    await events.set_label(db, device.id, night_id, event_id, label or None)
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}/events/{night_id}/{event_id}", status_code=303)


@router.get("/cameras/{device_id}/events.json")
async def export_events(db: DbSession, session: CurrentSession, device_id: str) -> Response:
    """Download of all events with labels (for tuning the detector)."""
    device = await _own(db, session, device_id)
    rows = [events.export_row(e) for e in await events.events(db, device.id, limit=100_000)]
    return JSONResponse(
        rows,
        headers={"Content-Disposition": f'attachment; filename="events-{device.id[:8]}.json"'},
    )
