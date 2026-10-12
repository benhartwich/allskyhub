"""Cameras of the signed-in user: list, pairing by code, latest image, live view, removal."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError

from allskyhub_protocol import CloseCode, FrameVariant
from allskyhub_server.auth import ratelimit
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import (
    events,
    health,
    night_summary,
    pairing,
    public,
    queries,
    settings_form,
    tonight,
)
from allskyhub_server.devices import settings as camera_settings
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.event_text import EVENT_TITLES, orientation_line, update_line
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Device
from allskyhub_server.web.deps import (
    CurrentSession,
    DbSession,
    OptionalSession,
    SettingsDep,
    client_ip,
    csrf_protect,
)
from allskyhub_server.web.render import render

router = APIRouter(dependencies=[Depends(csrf_protect)])


def _registry(request: Request) -> ConnectionRegistry:
    registry: ConnectionRegistry = request.app.state.connections
    return registry


async def _own_device(db: DbSession, session: SessionInfo, device_id: str) -> Device:
    """404 for unknown devices and devices of other accounts alike."""
    device = await queries.owned_one(db, session.user.id, device_id)
    if device is None:
        raise HTTPException(404)
    return device


@router.get("/")
async def home(request: Request, db: DbSession, session: OptionalSession) -> Response:
    """Signed in: the user's cameras. Otherwise the public start page."""
    if session is None:
        return render(request, "landing.html")
    devices = await queries.owned(db, session.user.id)
    registry = _registry(request)
    now = dt.datetime.now(dt.UTC)
    cameras = [
        (d, registry.is_online(d.id), health.check(d, registry.is_online(d.id), now))
        for d in devices
    ]
    return render(request, "home.html", {"cameras": cameras}, session=session)


@router.get("/cameras/pair")
async def pair_form(request: Request, session: CurrentSession, code: str = "") -> Response:
    return render(request, "pair.html", {"code": code}, session=session)


@router.post("/cameras/pair")
async def pair(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    session: CurrentSession,
    code: Annotated[str, Form(max_length=20)],
    name: Annotated[str, Form(max_length=100)] = "",
) -> Response:
    """SPEC §6.2 step 3 (fallback: typing the code)."""
    engine = request.app.state.engine
    try:
        await ratelimit.hit(engine, f"claim:user:{session.user.id}", ratelimit.CLAIM_PER_USER)
        await ratelimit.hit(
            engine, f"claim:ip:{client_ip(request, settings)}", ratelimit.CLAIM_PER_IP
        )
        device = await pairing.claim(db, session.user.id, code, name)
    except ratelimit.RateLimitedError:
        error, status = "Zu viele Versuche. Bitte später erneut versuchen.", 429
    except pairing.PairingError as exc:
        await db.rollback()
        error, status = str(exc), 400
    else:
        await db.commit()
        return RedirectResponse(f"/cameras/{device.id}", status_code=303)
    return render(
        request,
        "pair.html",
        {"code": code, "name": name, "error": error},
        session=session,
        status_code=status,
    )


@router.get("/cameras/{device_id}")
async def camera(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own_device(db, session, device_id)
    return render(
        request, "camera.html", await _camera_context(request, db, device), session=session
    )


async def _camera_context(
    request: Request,
    db: DbSession,
    device: Device,
    form: dict[str, str] | None = None,
    **extra: object,
) -> dict[str, object]:
    forecast: ForecastCache = request.app.state.forecast
    online = _registry(request).is_online(device.id)
    now = dt.datetime.now(dt.UTC)
    schema, own_schema = settings_form.schema_of(device)
    current = settings_form.current_values(device)
    return {
        "device": device,
        "tonight": await tonight.for_device(forecast, device.last_status, now),
        "last_night": await _last_night(db, device),
        "health": health.check(device, online, now),
        "online": online,
        "events": events.collapse(await events.events(db, device.id, limit=200, hide_false=True))[
            :6
        ],
        "event_titles": EVENT_TITLES,
        "settings_groups": settings_form.groups(schema, current, form),
        "own_schema": own_schema,
        "orientation": orientation_line(device.last_status),
        "update": update_line(device.last_status),
        "settings_reported": bool(current),
        **extra,
    }


async def _last_night(db: DbSession, device: Device) -> night_summary.NightSummary | None:
    """Roadmap C3: the night that ended this morning, if anything is known about it."""
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    zone = tonight.zone_for(settings.get("timezone"))
    night_id = night_summary.last_night_id(dt.datetime.now(dt.UTC), zone)
    summary = await night_summary.summarize(db, device.id, night_id)
    return None if summary.empty else summary


@router.get("/cameras/{device_id}/live")
async def live_fragment(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """Polled by the camera page while it is open: marks the device as watched, so the hub
    asks for every frame (SPEC §6.5)."""
    device = await _own_device(db, session, device_id)
    registry = _registry(request)
    registry.touch_live(device.id)
    return render(
        request,
        "_live.html",
        {"device": device, "online": registry.is_online(device.id)},
        session=session,
    )


@router.get("/cameras/{device_id}/image/{variant}.jpg")
async def image(
    request: Request, db: DbSession, session: CurrentSession, device_id: str, variant: FrameVariant
) -> Response:
    device = await _own_device(db, session, device_id)
    store: ImageStore = request.app.state.images
    path = store.path(device.id, variant)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/cameras/{device_id}/remove")
async def remove(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """SPEC §6.2 step 4: unpair; the device registers again and gets a new code."""
    device = await _own_device(db, session, device_id)
    await pairing.unpair(db, device)
    await db.commit()
    store: ImageStore = request.app.state.images
    store.delete_device(device.id)
    await _registry(request).close(device.id, CloseCode.UNPAIRED)
    return RedirectResponse("/", status_code=303)


@router.post("/cameras/{device_id}/public")
async def set_public(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    enabled: Annotated[bool, Form()],
) -> Response:
    """Opt-in public sky page (roadmap #9)."""
    device = await _own_device(db, session, device_id)
    if enabled:
        public.enable(device)
    else:
        public.disable(device)
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}", status_code=303)


PUBLIC_TEXTS = ("title", "location_label", "camera", "lens", "computer", "owner", "notes")
PUBLIC_SECTIONS = {
    "live": "Aktuelles Bild",
    "loop": "Die letzte Stunde als Loop",
    "events": "Meteore, Polarlicht und Blitze",
    "sky": "Himmelsqualität (Wolken, SQM, Sterne)",
    "aurora": "Polarlicht-Aussichten (Kp-Index)",
    "archive": "Nachtarchiv mit Keogramm und Sternspuren",
}


@router.get("/cameras/{device_id}/public/edit")
async def public_edit(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own_device(db, session, device_id)
    return render(
        request,
        "public_edit.html",
        {"device": device, "profile": public.profile(device), "sections": PUBLIC_SECTIONS},
        session=session,
    )


@router.post("/cameras/{device_id}/public/edit")
async def public_save(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """What the public page shows (devices/public.py PublicProfile)."""
    device = await _own_device(db, session, device_id)
    form = await request.form()
    data: dict[str, Any] = {key: str(form.get(key) or "") for key in PUBLIC_TEXTS}
    data["show_coordinates"] = form.get("show_coordinates") == "on"
    data["sections"] = {key: form.get(f"section_{key}") == "on" for key in PUBLIC_SECTIONS}
    try:
        profile = public.PublicProfile.model_validate(data)
    except ValidationError:
        return render(
            request,
            "public_edit.html",
            {
                "device": device,
                "profile": public.profile(device),
                "sections": PUBLIC_SECTIONS,
                "error": "Ein Text ist zu lang (Titel 100, Felder 200, Notizen 2000 Zeichen).",
            },
            session=session,
            status_code=400,
        )
    device.public_profile = profile.model_dump()
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}", status_code=303)


SAVED = "Gespeichert. Die Kamera startet die Aufnahme neu und meldet sich gleich wieder."


@router.post("/cameras/{device_id}/settings")
async def change_settings(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """SPEC §6.5 ``set_settings`` from the web UI, form from the schema (§6.9)."""
    device = await _own_device(db, session, device_id)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    schema, _ = settings_form.schema_of(device)
    diff, error = settings_form.changes(schema, settings_form.current_values(device), form)
    notice = None
    if error is None:
        outcome = await camera_settings.apply(
            _registry(request), device.id, diff, settings_form.field_labels(schema)
        )
        error = outcome.message
        if outcome.ok:
            notice = (
                "Gespeichert. Die Kamera startet die Aufnahme neu und meldet sich gleich wieder."
            )
    context = await _camera_context(request, db, device, form, error=error, notice=notice)
    return render(
        request, "camera.html", context, session=session, status_code=200 if error is None else 400
    )
