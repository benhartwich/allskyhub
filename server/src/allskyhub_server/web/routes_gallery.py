"""Gallery in the web UI (roadmap #13): nights, frames and night products, like in the app."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select

from allskyhub_protocol import EVENT_ID_PATTERN, FrameVariant
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import (
    archive,
    events,
    meteor_pairs,
    public,
    queries,
    renders,
    science,
    series,
    sky_chart,
    stats,
    target,
    tonight,
)
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.event_text import EVENT_TITLES, event_rows, meteor_summary
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.queries import Role
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Device, EventRecord, RenderJob
from allskyhub_server.web.deps import CurrentSession, DbSession, csrf_protect
from allskyhub_server.web.render import render
from allskyhub_sky.objects import catalogue

router = APIRouter(dependencies=[Depends(csrf_protect)])

NightId = Annotated[str, Path(pattern=archive.NIGHT_ID)]
ProductName = Annotated[str, Path(pattern=archive.PRODUCT_NAME)]
EventId = Annotated[str, Path(pattern=EVENT_ID_PATTERN)]
PRODUCT_TITLES = {"keogram": "Keogramm", "startrails": "Sternspuren", "timelapse": "Zeitraffer"}


async def _access(
    db: DbSession, session: SessionInfo, device_id: str, need: Role = "viewer"
) -> tuple[Device, Role]:
    """The camera and the user's role (roadmap C10); 404 below ``need``."""
    found = await queries.visible_one(db, session.user.id, device_id, need)
    if found is None:
        raise HTTPException(404)
    return found


async def _own(
    db: DbSession, session: SessionInfo, device_id: str, need: Role = "viewer"
) -> Device:
    """Everything in the gallery can be seen by everyone the camera is shared with."""
    return (await _access(db, session, device_id, need))[0]


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
    night_events = await events.events(
        db, device.id, night_id=night_id, limit=2000, hide_false=True
    )
    return render(
        request,
        "night.html",
        {
            "device": device,
            "night_id": night_id,
            "frames": await archive.night_frames(db, device.id, night_id),
            "sky_charts": sky_chart.charts(await archive.night_sky(db, device.id, night_id))
            + sky_chart.charts(
                await archive.night_status(db, device.id, *_night_window(device, night_id)),
                sky_chart.STATUS_SERIES,
            ),
            "products": await archive.night_products(db, device.id, night_id),
            "events": events.collapse(night_events),
            "meteors": meteor_summary(night_events),
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
            "events": events.collapse(rows[50 * (page - 1) : 50 * page]),
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
    seen_too: list[dict[str, object]] = []
    for pair, other in await meteor_pairs.for_event(db, device.id, event.event_id):
        own = other.owner_id == device.owner_id
        other_event = pair.event_b if pair.device_a == device.id else pair.event_a
        seen_too.append({
            "pair": pair,
            "name": other.name if own else public.title(other),
            "link": (f"/cameras/{other.id}/events/{other_event[7:15]}/{other_event}" if own
                     else f"/sky/{other.public_slug}" if other.public_slug else None),
        })  # fmt: skip
    return render(
        request,
        "event.html",
        {
            "device": device,
            "event": event,
            "state": await _event_state(request, device, event),
            "rows": event_rows(event),
            "seen_too": seen_too,
            "fireball": science.fireball_facts(event),
            "fireball_form": science.FIREBALL_FORM,
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
    """ "Kein Meteor" / "Echter Meteor" / reset; ``label`` "" clears it (owner, admin)."""
    device = await _own(db, session, device_id, "admin")
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


@router.get("/cameras/{device_id}/storms/{storm}")
async def storm_page(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    storm: Annotated[str, Path(pattern=events.STORM_PATTERN)],
) -> Response:
    """All flashes of one thunderstorm (SPEC §6.4 lightning ``data.storm``)."""
    device = await _own(db, session, device_id)
    flashes = await events.storm_flashes(db, device.id, storm)
    if not flashes:
        raise HTTPException(404)
    return render(
        request,
        "storm.html",
        {
            "device": device,
            "storm": events.Storm(storm, flashes),
            "event_titles": EVENT_TITLES,
        },
        session=session,
    )


@router.get("/cameras/{device_id}/stats")
async def statistics(
    request: Request, db: DbSession, session: CurrentSession, device_id: str, year: int = 0
) -> Response:
    """Roadmap C7: clear nights, sky brightness and detections per month."""
    device = await _own(db, session, device_id)
    this_year = dt.datetime.now(dt.UTC).year
    chosen = year if 2000 <= year <= this_year + 1 else this_year
    return render(
        request,
        "stats.html",
        {"device": device, "stats": await stats.year(db, device.id, chosen)},
        session=session,
    )


NIGHT_PATTERN = r"^\d{8}$"


@router.get("/cameras/{device_id}/charts")
async def charts_page(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """The measurement viewer: sky values and the camera's own state over a chosen range."""
    device = await _own(db, session, device_id)
    return render(
        request,
        "charts.html",
        {
            "device": device,
            "data_url": f"/cameras/{device.id}/charts/data",
            "nights_url": f"/cameras/{device.id}/charts/nights",
            "back_url": f"/cameras/{device.id}",
            "back_title": device.name,
            "with_status": True,
        },
        session=session,
    )


@router.get("/cameras/{device_id}/charts/data")
async def charts_data(
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    night: Annotated[str | None, Query(pattern=NIGHT_PATTERN)] = None,
    hours: int | None = None,
) -> JSONResponse:
    device = await _own(db, session, device_id)
    rng = await series.resolve(db, device, night, hours, dt.datetime.now(dt.UTC))
    data = await series.load(db, device, rng, with_status=True, public=False)
    return JSONResponse(data, headers={"Cache-Control": "private, no-store"})


@router.get("/cameras/{device_id}/charts/nights")
async def charts_nights(
    db: DbSession, session: CurrentSession, device_id: str, year: int = 0
) -> JSONResponse:
    """Roadmap C7 over a year: darkest sky and clear hours per night, with the moon."""
    device = await _own(db, session, device_id)
    this_year = dt.datetime.now(dt.UTC).year
    chosen = year if 2000 <= year <= this_year + 1 else this_year
    data = await series.nights(db, device, chosen)
    return JSONResponse(data, headers={"Cache-Control": "private, no-store"})


def _night_window(device: Device, night_id: str) -> tuple[dt.datetime, dt.datetime]:
    """Local noon of the evening to noon of the next day (SPEC §4.5 night id)."""
    zone = _zone(device)
    evening = dt.datetime.strptime(night_id, "%Y%m%d").replace(hour=12, tzinfo=zone)
    return evening.astimezone(dt.UTC), (evening + dt.timedelta(days=1)).astimezone(dt.UTC)


def _zone(device: Device) -> ZoneInfo:
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    return tonight.zone_for(settings.get("timezone"))


async def _render_page(
    request: Request, db: DbSession, session: SessionInfo, device: Device, **extra: object
) -> Response:
    jobs = list(
        await db.scalars(
            select(RenderJob)
            .where(RenderJob.device_id == device.id)
            .order_by(RenderJob.created_at.desc())
        )
    )
    zone = _zone(device)
    now = dt.datetime.now(zone)
    evening = (now - dt.timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    return render(
        request,
        "renders.html",
        {
            "device": device,
            "jobs": jobs,
            "zone": zone,
            "busy": any(j.state in ("queued", "running") for j in jobs),
            "default_start": evening.strftime("%Y-%m-%dT%H:%M"),
            "default_end": (evening + dt.timedelta(hours=14)).strftime("%Y-%m-%dT%H:%M"),
            "default_from": (now.date() - dt.timedelta(days=30)).isoformat(),
            "default_to": now.date().isoformat(),
            "role": "admin",
            **extra,
        },
        session=session,
        status_code=400 if extra.get("error") else 200,
    )


@router.get("/cameras/{device_id}/renders")
async def renders_page(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """Roadmap C8: timelapse over a chosen period, keogram over several nights."""
    device, role = await _access(db, session, device_id)
    return await _render_page(request, db, session, device, role=role)


@router.get("/cameras/{device_id}/renders/list")
async def renders_list(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """Polled while a job runs."""
    device = await _own(db, session, device_id)
    jobs = list(
        await db.scalars(
            select(RenderJob)
            .where(RenderJob.device_id == device.id)
            .order_by(RenderJob.created_at.desc())
        )
    )
    busy = any(j.state in ("queued", "running") for j in jobs)
    context = {"device": device, "jobs": jobs, "zone": _zone(device), "busy": busy}
    return render(request, "_render_jobs.html", context, session=session)


def _local(value: str, zone: ZoneInfo) -> dt.datetime:
    return dt.datetime.fromisoformat(value).replace(tzinfo=zone).astimezone(dt.UTC)


@router.post("/cameras/{device_id}/renders")
async def render_request(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    kind: Annotated[str, Form()],
    start: Annotated[str, Form(max_length=20)] = "",
    end: Annotated[str, Form(max_length=20)] = "",
    fps: Annotated[int, Form()] = 25,
) -> Response:
    device = await _own(db, session, device_id, "admin")
    zone = _zone(device)
    try:
        if kind == "keogram":
            begin = _local(f"{start}T00:00", zone)
            finish = _local(f"{end}T23:59", zone)
        else:
            begin, finish = _local(start, zone), _local(end, zone)
        await renders.request(db, device, kind, begin, finish, fps)
    except ValueError:
        return await _render_page(
            request, db, session, device, error="Bitte Datum und Uhrzeit angeben."
        )
    except renders.RenderError as exc:
        return await _render_page(request, db, session, device, error=str(exc))
    await db.commit()
    request.app.state.renderer.wake()
    return RedirectResponse(f"/cameras/{device.id}/renders", status_code=303)


async def _job(db: DbSession, device: Device, job_id: str) -> RenderJob:
    try:
        job = await db.get(RenderJob, uuid.UUID(job_id))
    except ValueError:
        job = None
    if job is None or job.device_id != device.id:
        raise HTTPException(404)
    return job


@router.get("/cameras/{device_id}/renders/{job_id}/file", response_model=None)
async def render_file(
    request: Request, db: DbSession, session: CurrentSession, device_id: str, job_id: str
) -> Response:
    device = await _own(db, session, device_id)
    job = await _job(db, device, job_id)
    store: ImageStore = request.app.state.images
    path = store.render_path(device.id, str(job.id), renders.SUFFIX[job.kind])
    if job.state != "done" or not path.is_file():
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type=renders.MEDIA[job.kind],
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/cameras/{device_id}/renders/{job_id}/delete")
async def render_delete(
    request: Request, db: DbSession, session: CurrentSession, device_id: str, job_id: str
) -> Response:
    device = await _own(db, session, device_id, "admin")
    job = await _job(db, device, job_id)
    if job.state == "running":
        return await _render_page(request, db, session, device, error="Läuft gerade, bitte warten.")
    store: ImageStore = request.app.state.images
    store.render_path(device.id, str(job.id), renders.SUFFIX[job.kind]).unlink(missing_ok=True)
    await db.delete(job)
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}/renders", status_code=303)


@router.get("/cameras/{device_id}/export")
async def export_page(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """Roadmap C11: downloads for science."""
    device = await _own(db, session, device_id)
    context = {
        "device": device,
        "exports": science.EXPORTS,
        "globe_at_night": science.GLOBE_AT_NIGHT,
    }
    return render(request, "export.html", context, session=session)


@router.get("/cameras/{device_id}/export/{name}.csv")
async def export_csv(db: DbSession, session: CurrentSession, device_id: str, name: str) -> Response:
    device = await _own(db, session, device_id)
    if name not in science.EXPORTS:
        raise HTTPException(404)
    body, filename = await science.export(db, device, name)
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/cameras/{device_id}/ziel")
async def target_page(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    objekt: Annotated[str, Query(max_length=20)] = "",
) -> Response:
    """Roadmap H4: is an object clear now and in the next hours?"""
    device = await _own(db, session, device_id)
    chosen = target.find(objekt) if objekt else None
    found = None
    if chosen is not None:
        settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
        lat, lon = settings.get("latitude"), settings.get("longitude")
        hours: list[tonight.Hour] = []
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            forecast: ForecastCache = request.app.state.forecast
            hours = await forecast.get(float(lat), float(lon))
        found = target.plan(chosen, device.last_status, hours, dt.datetime.now(dt.UTC))
    return render(
        request,
        "target.html",
        {
            "device": device,
            "objects": catalogue(),
            "chosen": chosen,
            "plan": found,
            "unknown": bool(objekt) and chosen is None,
            "verdicts": target.VERDICT_TEXT,
        },
        session=session,
    )
