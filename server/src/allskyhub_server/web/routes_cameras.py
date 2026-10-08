"""Cameras of the signed-in user: list, pairing by code, latest image, live view, removal."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError

from allskyhub_protocol import CloseCode, FrameVariant, SetSettingsArgs
from allskyhub_server.auth import ratelimit
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import events, pairing, public, queries
from allskyhub_server.devices import settings as camera_settings
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.event_text import EVENT_TITLES
from allskyhub_server.devices.images import ImageStore
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
    cameras = [(d, registry.is_online(d.id)) for d in devices]
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
        request,
        "camera.html",
        {
            "device": device,
            "online": _registry(request).is_online(device.id),
            "events": events.collapse(
                await events.events(db, device.id, limit=200, hide_false=True)
            )[:6],
            "event_titles": EVENT_TITLES,
            "camera_choices": camera_settings.CAMERA_CHOICES,
            "settings": settings_values(device),
            "settings_reported": bool((device.last_status or {}).get("settings")),
        },
        session=session,
    )


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


SETTING_KEYS = ("latitude", "longitude", "timezone", "camera", "day_delay_s", "night_delay_s")


def settings_values(device: Device, form: dict[str, str] | None = None) -> dict[str, str]:
    """What the settings form shows: the submitted values, else the camera's current ones."""
    reported = (device.last_status or {}).get("settings")
    current: dict[str, Any] = cast(dict[str, Any], reported) if isinstance(reported, dict) else {}
    values: dict[str, str] = {}
    for key in SETTING_KEYS:
        if form and key in form:
            values[key] = form[key]
        elif current.get(key) is not None:
            values[key] = str(current[key])
        else:
            values[key] = ""
    return values


def _settings_form(form: dict[str, str]) -> SetSettingsArgs | str:
    """The settings form: empty fields stay unchanged. Returns the args or a German error."""
    values: dict[str, object] = {}
    lat, lon = form.get("latitude", "").strip(), form.get("longitude", "").strip()
    if lat or lon:
        try:
            values["latitude"] = float(lat.replace(",", "."))
            values["longitude"] = float(lon.replace(",", "."))
        except ValueError:
            return "Bitte Breite und Länge als Zahlen angeben (beide oder keine)."
    for key in ("timezone", "camera"):
        if value := form.get(key, "").strip():
            values[key] = value
    for key in ("day_delay_s", "night_delay_s"):
        if value := form.get(key, "").strip():
            try:
                values[key] = float(value.replace(",", "."))
            except ValueError:
                return f"{camera_settings.FIELD_NAMES[key]}: bitte eine Zahl in Sekunden."
    try:
        return SetSettingsArgs.model_validate(values)
    except ValidationError as exc:
        fields = {str(e["loc"][0]) for e in exc.errors() if e["loc"]} or {"latitude", "longitude"}
        return f"Ungültige Angabe: {camera_settings.describe_fields(', '.join(sorted(fields)))}."


@router.post("/cameras/{device_id}/settings")
async def change_settings(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """SPEC §6.5 ``set_settings`` from the web UI (roadmap #2)."""
    device = await _own_device(db, session, device_id)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    parsed = _settings_form(form)
    if isinstance(parsed, str):
        error, notice = parsed, None
    else:
        outcome = await camera_settings.apply(_registry(request), device.id, parsed)
        error = outcome.message
        notice = (
            "Gespeichert. Die Kamera startet die Aufnahme neu und meldet sich gleich wieder."
            if outcome.ok
            else None
        )
    return render(
        request,
        "camera.html",
        {
            "device": device,
            "online": _registry(request).is_online(device.id),
            "events": events.collapse(
                await events.events(db, device.id, limit=200, hide_false=True)
            )[:6],
            "event_titles": EVENT_TITLES,
            "camera_choices": camera_settings.CAMERA_CHOICES,
            "settings": settings_values(device, form),
            "settings_reported": bool((device.last_status or {}).get("settings")),
            "error": error,
            "notice": notice,
        },
        session=session,
        status_code=200 if error is None else 400,
    )
