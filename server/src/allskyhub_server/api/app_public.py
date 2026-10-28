"""Public cameras for the app without login (the app's "Entdecken").

Exactly what the public pages (/sky/<slug>, roadmap #9) and the camera map (roadmap C5)
already show, as JSON: a camera is listed when its owner shows it on the start page or
shows its place on the map; one with only a link stays unlisted, but its slug opens it
like the page does. The pictures are the public page's own URLs (``/sky/<slug>/…``), so
nothing here reaches further than the web.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from allskyhub_server.devices import archive, events, public
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.event_text import EVENT_TITLES
from allskyhub_server.i18n import tr
from allskyhub_server.models import Device, EventRecord
from allskyhub_server.web.deps import DbSession

router = APIRouter(prefix="/api/v1/public")

# The answers change with every picture (about every five minutes).
CACHE = "public, max-age=60"
FEED_MAX = 30
FEED_DAYS = 3
NIGHTS_MAX = 14
EVENTS_MAX = 24
# English next to event_text.EVENT_TITLES, for the public kinds.
TITLES_EN = {"meteor": "Meteor", "aurora": "Aurora", "nlc": "Noctilucent clouds",
             "lightning": "Lightning"}  # fmt: skip


class PublicCamera(BaseModel):
    slug: str
    title: str
    place: str
    # Rounded to 0.1° and only when the owner shows the place (public.coordinates).
    lat: float | None
    lon: float | None
    # Like the facts: only with the live image public, else None (the page shows neither).
    online: bool | None
    # None when there is no picture or the owner does not show the live image.
    image_at: dt.datetime | None
    night: bool | None  # from the latest frame's mode


class Facts(BaseModel):
    """The latest picture's facts, like under the image on the public page."""

    mode: str | None = None
    sun_elevation: float | None = None
    exposure_s: float | None = None
    gain: float | None = None
    cloud_cover: float | None = None  # 0..1
    sqm_mag: float | None = None
    stars: int | None = None


class LoopFrame(BaseModel):
    at: dt.datetime
    url: str


class NightProduct(BaseModel):
    name: str
    url: str  # thumbnail; ``?full=1`` gives the full image


class PublicEvent(BaseModel):
    slug: str
    camera: str
    kind: str
    title: str
    at: dt.datetime
    night_id: str
    url: str


class PublicCameraDetail(PublicCamera):
    location_label: str
    camera: str
    lens: str
    computer: str
    owner: str
    notes: str
    sections: dict[str, bool]
    facts: Facts | None
    live_url: str | None
    page_url: str
    loop: list[LoopFrame]
    last_night: str | None
    products: list[NightProduct]
    video_url: str | None
    events: list[PublicEvent]
    nights: list[str]


class Feed(BaseModel):
    events: list[PublicEvent]


def _registry(request: Request) -> ConnectionRegistry:
    registry: ConnectionRegistry = request.app.state.connections
    return registry


def _json(model: BaseModel) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), headers={"Cache-Control": CACHE})


def _listed(device: Device) -> bool:
    return device.public_on_home or public.coordinates(device) is not None


def _camera(device: Device, registry: ConnectionRegistry) -> dict[str, Any]:
    slug = device.public_slug or ""
    profile = public.profile(device)
    coords = public.coordinates(device)
    mode = (device.last_frame or {}).get("mode")
    live = profile.sections.live
    return {
        "slug": slug,
        "title": public.title(device),
        "place": profile.location_label,
        "lat": coords[0] if coords else None,
        "lon": coords[1] if coords else None,
        "online": registry.is_online(device.id) if live else None,
        "image_at": device.latest_image_at if live else None,
        "night": mode == "night" if live and isinstance(mode, str) else None,
    }


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _facts(device: Device) -> Facts | None:
    frame: dict[str, Any] = device.last_frame or {}
    sky: dict[str, Any] = (device.last_status or {}).get("sky") or {}
    if not frame and not sky:
        return None
    exposure = _number(frame.get("exposure_us"))
    stars = sky.get("stars")
    mode = frame.get("mode")
    return Facts(
        mode=mode if isinstance(mode, str) else None,
        sun_elevation=_number(frame.get("sun_elevation")),
        exposure_s=exposure / 1e6 if exposure is not None else None,
        gain=_number(frame.get("gain")),
        cloud_cover=_number(sky.get("cloud_cover")),
        sqm_mag=_number(sky.get("sqm_mag")),
        stars=stars if isinstance(stars, int) else None,
    )


def _event(device: Device, e: EventRecord) -> PublicEvent:
    slug = device.public_slug or ""
    return PublicEvent(
        slug=slug,
        camera=public.title(device),
        kind=e.kind,
        title=tr(EVENT_TITLES.get(e.kind, e.kind), TITLES_EN.get(e.kind, e.kind)),
        at=e.start,
        night_id=e.night_id,
        url=f"/sky/{slug}/events/{e.night_id}/{e.event_id}.jpg?v={events.image_rev(e.data)}",
    )


async def _public_devices(db: DbSession) -> list[Device]:
    rows = await db.scalars(
        select(Device).where(Device.public_slug.is_not(None), Device.owner_id.is_not(None))
    )
    return [d for d in rows if _listed(d)]


@router.get("/cameras")
async def cameras(request: Request, db: DbSession) -> JSONResponse:
    """The listed public cameras, the freshest picture first."""
    registry = _registry(request)
    rows = [PublicCamera(**_camera(d, registry)) for d in await _public_devices(db)]
    far_past = dt.datetime.min.replace(tzinfo=dt.UTC)
    rows.sort(key=lambda c: (c.image_at or far_past, c.title.lower()), reverse=True)
    return JSONResponse([c.model_dump(mode="json") for c in rows], headers={"Cache-Control": CACHE})


@router.get("/feed")
async def feed(db: DbSession) -> JSONResponse:
    """ "Gerade passiert": the newest detections of the listed cameras that show them."""
    devices = {d.id: d for d in await _public_devices(db) if public.profile(d).sections.events}
    if not devices:
        return _json(Feed(events=[]))
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=FEED_DAYS)
    rows = await db.scalars(
        select(EventRecord)
        .where(
            EventRecord.device_id.in_(devices),
            EventRecord.kind.in_(public.PUBLIC_EVENT_KINDS),
            EventRecord.has_thumb.is_(True),
            EventRecord.start >= since,
            events.counted(),
        )
        .order_by(EventRecord.start.desc())
        .limit(FEED_MAX)
    )
    return _json(Feed(events=[_event(devices[e.device_id], e) for e in rows]))


@router.get("/cameras/{slug}")
async def camera(request: Request, db: DbSession, slug: str) -> JSONResponse:
    device = await public.by_slug(db, slug)
    if device is None:
        raise HTTPException(404)
    profile = public.profile(device)
    sections = profile.sections
    now = dt.datetime.now(dt.UTC)
    night_id, products = await public.newest_products(db, device)
    video = await public.night_video(db, device, night_id) if night_id else None
    loop = await public.loop_frames(db, device, now) if sections.loop else []
    recent = await public.recent_events(db, device, limit=EVENTS_MAX) if sections.events else []
    nights = (
        [n.night_id for n in await archive.nights(db, device.id) if n.frames or n.products][
            :NIGHTS_MAX
        ]
        if sections.archive
        else []
    )
    image_at = device.latest_image_at if sections.live else None
    detail = PublicCameraDetail(
        **_camera(device, _registry(request)),
        location_label=profile.location_label,
        camera=profile.camera,
        lens=profile.lens,
        computer=profile.computer,
        owner=profile.owner,
        notes=profile.notes,
        sections=sections.model_dump(),
        facts=_facts(device) if sections.live else None,
        live_url=(f"/sky/{slug}/latest.jpg?t={int(image_at.timestamp())}" if image_at else None),
        page_url=f"/sky/{slug}",
        loop=[
            LoopFrame(at=f.captured_at, url=f"/sky/{slug}/frames/{f.night_id}/{f.name}")
            for f in loop
        ],
        last_night=night_id if sections.archive else None,
        products=[
            NightProduct(name=p.name, url=f"/sky/{slug}/products/{p.night_id}/{p.name}")
            for p in products
        ]
        if sections.archive
        else [],
        video_url=f"/sky/{slug}/nights/{night_id}/timelapse.mp4" if video else None,
        events=[_event(device, e) for e in recent],
        nights=nights,
    )
    return _json(detail)
