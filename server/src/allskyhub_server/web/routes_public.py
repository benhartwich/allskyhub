"""Public sky pages (/sky/<slug>): no login, only what the owner published (roadmap #9)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from allskyhub_protocol import EventVariant, FrameVariant
from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import (
    aircraft,
    archive,
    map_tiles,
    public,
    series,
    sky_chart,
    space_weather,
    tonight,
)
from allskyhub_server.devices import camera_map as camera_map_mod
from allskyhub_server.devices.event_text import EVENT_TITLES, meteor_summary
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.map_tiles import MapTiles
from allskyhub_server.devices.space_weather import KpCache, OvationCache
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Device
from allskyhub_server.web.deps import DbSession, OptionalSession, SettingsDep, client_ip
from allskyhub_server.web.render import render
from allskyhub_server.web.templating import templates

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


async def _sky_device(db: DbSession, slug: str) -> Device:
    """The device, if its public page shows sky values."""
    device = await _device(db, slug)
    if not public.profile(device).sections.sky:
        raise HTTPException(404)
    return device


@router.get("/sky/{slug}/charts")
async def sky_charts_page(
    request: Request, session: OptionalSession, db: DbSession, slug: str
) -> Response:
    """The measurement viewer on the public page: sky values only, no camera internals."""
    device = await _sky_device(db, slug)
    return render(
        request,
        "charts.html",
        {
            **_base(device, slug),
            "data_url": f"/sky/{slug}/charts/data",
            "nights_url": None,
            "back_url": f"/sky/{slug}",
            "back_title": public.title(device),
            "with_status": False,
        },
        session=session,
    )


@router.get("/sky/{slug}/charts/data")
async def sky_charts_data(
    db: DbSession,
    slug: str,
    night: Annotated[str | None, Query(pattern=r"^\d{8}$")] = None,
    hours: int | None = None,
) -> JSONResponse:
    device = await _sky_device(db, slug)
    rng = await series.resolve(db, device, night, hours, dt.datetime.now(dt.UTC))
    data = await series.load(db, device, rng, with_status=False, public=True)
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=60"})


@router.get("/sky/{slug}")
async def sky_page(
    request: Request, session: OptionalSession, db: DbSession, slug: str
) -> Response:
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
    forecast: ForecastCache = request.app.state.forecast
    night = (
        await tonight.for_device(
            forecast, public.coarse_status(device), now, request.app.state.tles
        )
        if sections.sky
        else None
    )
    return render(
        request,
        "public_sky.html",
        {
            **_base(device, slug),
            "night_id": night_id,
            "products": products if sections.archive else [],
            "video": await public.night_video(db, device, night_id) if night_id else None,
            "loop_frames": await public.loop_frames(db, device, now) if sections.loop else [],
            "events": events,
            "nights": nights if sections.archive else [],
            "sky_night": sky_night,
            "sky_charts": charts,
            "kp": kp,
            "tonight": night,
            "observatory": public.observatory(device) if sections.observatory else None,
            "today": now.date(),
            "kp_needed": space_weather.needed_kp(lat) if lat is not None else None,
            "hemisphere": "south" if lat is not None and lat < 0 else "north",
            "base_url": str(request.base_url).rstrip("/"),
        },
        session=session,
    )


@router.get("/sky/{slug}/now")
async def sky_now(request: Request, db: DbSession, slug: str) -> Response:
    """The latest image and its facts; polled by the page."""
    device = await _device(db, slug)
    profile = public.profile(device)
    if not profile.sections.live:
        raise HTTPException(404)
    if profile.sections.aircraft and aircraft.current is not None:
        aircraft.current.watched(device.id)  # SPEC §7.8: someone watches the live image
    return render(request, "_public_now.html", {"device": device, "slug": slug, "profile": profile})


@router.get("/sky/{slug}/latest.jpg")
async def sky_latest(request: Request, db: DbSession, slug: str, thumb: bool = False) -> Response:
    """The latest picture; ``thumb=1`` for lists such as the app's "Entdecken"."""
    device = await _device(db, slug)
    if not public.profile(device).sections.live:
        raise HTTPException(404)  # the owner switched the live image off
    store: ImageStore = request.app.state.images
    variant = FrameVariant.THUMB if thumb else FrameVariant.FULL
    return _jpeg(store.path(device.id, variant), PUBLIC_CACHE)


@router.get("/sky/{slug}/nights/{night_id}")
async def sky_night(
    request: Request, session: OptionalSession, db: DbSession, slug: str, night_id: NightId
) -> Response:
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
            "video": await public.night_video(db, device, night_id),
            "frames": await archive.night_frames(db, device.id, night_id),
            "events": events if sections.events else [],
            "meteors": meteor_summary(events) if sections.events else None,
            "sky_charts": (
                sky_chart.charts(await archive.night_sky(db, device.id, night_id))
                if sections.sky
                else []
            ),
        },
        session=session,
    )


@router.get("/sky/{slug}/products/{night_id}/{name}")
async def sky_product(
    request: Request, db: DbSession, slug: str, night_id: str, name: str, full: bool = False
) -> Response:
    device = await _device(db, slug)
    if not public.profile(device).sections.archive:
        raise HTTPException(404)
    if name not in public.PUBLIC_PRODUCTS or not night_id.isdigit() or len(night_id) != 8:
        raise HTTPException(404)
    store: ImageStore = request.app.state.images
    variant = FrameVariant.FULL if full else FrameVariant.THUMB
    return _jpeg(store.product_path(device.id, night_id, name, variant), PUBLIC_CACHE)


@router.get("/sky/{slug}/nights/{night_id}/timelapse.mp4")
async def sky_video(request: Request, db: DbSession, slug: str, night_id: NightId) -> Response:
    """The night's timelapse (with range requests, so players can seek), only when the
    page shows its archive and the hub holds the file; never fetched for a visitor."""
    device = await _device(db, slug)
    video = await public.night_video(db, device, night_id)
    store: ImageStore = request.app.state.images
    path = store.product_path(device.id, night_id, video.name, FrameVariant.FULL) if video else None
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": ARCHIVE_CACHE})


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
    return _jpeg(store.event_path(device.id, night_id, event_id, EventVariant.THUMB), PUBLIC_CACHE)


# Roadmap C9: embedding on other websites. Only these answers may be framed by anyone.
EMBED_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "frame-ancestors *; base-uri 'self'; form-action 'self'"
)


@router.get("/sky/{slug}/widget")
async def sky_widget(request: Request, db: DbSession, slug: str) -> Response:
    """A small page with the latest image for an <iframe> on another website."""
    device = await _device(db, slug)
    if not public.profile(device).sections.live:
        raise HTTPException(404)
    response = render(request, "public_widget.html", {**_base(device, slug)})
    response.headers["Content-Security-Policy"] = EMBED_CSP
    response.headers["X-Frame-Options"] = "ALLOWALL"  # ignored by browsers; CSP decides
    return response


@router.get("/sky/{slug}/events.atom")
async def sky_events_feed(request: Request, db: DbSession, slug: str) -> Response:
    """Atom feed of the detections shown on the public page (roadmap C9)."""
    device = await _device(db, slug)
    if not public.profile(device).sections.events:
        raise HTTPException(404)
    events = await public.recent_events(db, device, limit=30)
    base = str(request.base_url).rstrip("/")
    body = templates.get_template("public_events.atom.xml").render(
        {**_base(device, slug), "events": events, "base_url": base,
         "updated": events[0].start if events else device.paired_at or dt.datetime.now(dt.UTC)}
    )  # fmt: skip
    return Response(
        body,
        media_type="application/atom+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/space-weather/ovation/{hemisphere}.jpg")
async def ovation(request: Request, hemisphere: str) -> Response:
    """NOAA's aurora forecast map through the hub (roadmap C13)."""
    cache: OvationCache = request.app.state.ovation
    image = await cache.get(hemisphere)
    if image is None:
        raise HTTPException(404)
    return Response(
        image, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=600"}
    )


@router.get("/karte/kacheln/{layer}/{z}/{x}/{y}")
async def map_tile(request: Request, layer: str, z: int, x: int, y: int) -> Response:
    """Roadmap C5: map tiles through the hub, so no map service sees a visitor."""
    tiles: MapTiles = request.app.state.map_tiles
    body = await tiles.tile(layer, z, x, y)
    if body is None:
        raise HTTPException(404)
    return Response(
        body,
        media_type=map_tiles.LAYERS[layer].media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@router.get("/karte/suche")
async def map_search(request: Request, settings: SettingsDep, q: str = "") -> Response:
    """Places for the map's search box (Nominatim through the hub)."""
    try:
        await ratelimit.hit(
            request.app.state.engine,
            f"map-search:{client_ip(request, settings)}",
            ratelimit.MAP_SEARCH_PER_IP,
        )
    except ratelimit.RateLimitedError:
        return JSONResponse({"detail": "Zu viele Suchen. Bitte kurz warten."}, status_code=429)
    tiles: MapTiles = request.app.state.map_tiles
    return JSONResponse(await tiles.search(q))


@router.get("/karte")
async def camera_map(
    request: Request,
    session: OptionalSession,
    db: DbSession,
    region: str = "europa",
    layer: str = "",
) -> Response:
    """Roadmap C5: all public cameras that show their place, on OpenStreetMap or a
    satellite map; ``layer=sqm`` colours them by how dark their sky is over the night
    lights (roadmap H4)."""
    region = region if region in camera_map_mod.REGIONS else "europa"
    _, lat, lon, zoom = camera_map_mod.REGIONS[region]
    cameras = await camera_map_mod.cameras(db)
    config = {
        "view": [lat, lon, zoom],
        "layer": "sqm" if layer == "sqm" else "",
        "maxZoom": map_tiles.MAX_ZOOM,
        "layers": {
            key: {"maxZoom": t.max_zoom, "credit": t.credit} for key, t in map_tiles.LAYERS.items()
        },
        "cameras": [c.as_json() for c in cameras],
    }
    return render(
        request,
        "camera_map.html",
        {
            "cameras": cameras,
            "regions": camera_map_mod.REGIONS,
            "region": region,
            "layer": config["layer"],
            "sqm_classes": camera_map_mod.SQM_CLASSES,
            "map_config": config,
        },
        session=session,
    )
