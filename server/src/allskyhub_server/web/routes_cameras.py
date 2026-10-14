"""Cameras of the signed-in user: list, pairing by code, latest image, live view, removal."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError

from allskyhub_protocol import CloseCode, FrameVariant
from allskyhub_server.auth import ratelimit
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.billing import plus
from allskyhub_server.devices import (
    archive,
    camera_map,
    discord,
    domains,
    events,
    health,
    night_summary,
    nowcast,
    pairing,
    public,
    queries,
    settings_form,
    sharing,
    tonight,
)
from allskyhub_server.devices import settings as camera_settings
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.event_text import EVENT_TITLES, orientation_line, update_line
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.queries import Role
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Device, User
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


async def _visible(
    db: DbSession, session: SessionInfo, device_id: str, need: Role = "viewer"
) -> tuple[Device, Role]:
    """Roadmap C10: shared cameras; 404 below ``need``."""
    found = await queries.visible_one(db, session.user.id, device_id, need)
    if found is None:
        raise HTTPException(404)
    return found


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
        return render(request, "landing.html", {"cameras": await camera_map.home_cameras(db)})
    registry = _registry(request)
    now = dt.datetime.now(dt.UTC)
    cameras = [
        (d, registry.is_online(d.id), health.check(d, registry.is_online(d.id), now), role)
        for d, role in await queries.visible(db, session.user.id)
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
    device, role = await _visible(db, session, device_id)
    context = await _camera_context(request, db, device, role=role)
    return render(request, "camera.html", context, session=session)


async def _trial_free(db: DbSession, device: Device) -> bool:
    """Whether the owner's account may still start the free Plus month."""
    if device.owner_id is None:
        return False
    owner = await db.get(User, device.owner_id)
    return owner is not None and owner.plus_trial_at is None


async def _camera_context(
    request: Request,
    db: DbSession,
    device: Device,
    form: dict[str, str] | None = None,
    role: Role = "owner",
    **extra: object,
) -> dict[str, object]:
    forecast: ForecastCache = request.app.state.forecast
    online = _registry(request).is_online(device.id)
    now = dt.datetime.now(dt.UTC)
    schema, own_schema = settings_form.schema_of(device)
    current = settings_form.current_values(device)
    return {
        "device": device,
        "public_profile": public.profile(device),
        "public_coordinates": public.coordinates(device),
        "tonight": await tonight.for_device(
            forecast, device.last_status, now, request.app.state.tles
        ),
        "nowcast": await nowcast.for_device(db, forecast, device, now),
        "last_night": await _last_night(db, device),
        "health": await health.full_check(db, device, online, now, forecast),
        "paused": health.paused(device),
        "reboot_required": health.reboot_required(device),
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
        "role": role,
        "observatory": public.observatory(device),
        "charts": await archive.recent_charts(db, device.id, now - dt.timedelta(hours=24)),
        "members": await sharing.members(db, device.id) if role == "owner" else [],
        "discord": discord.settings_of(device) if role == "owner" else None,
        "plus": await plus.state(db, device, now) if role == "owner" else None,
        "plus_message": plus.MESSAGES.get(request.query_params.get("plus", "")),
        "plus_trial_free": role == "owner" and await _trial_free(db, device),
        "billing": request.app.state.settings.billing,
        "price_month": plus.PRICE_MONTH,
        "price_year": plus.PRICE_YEAR,
        "discord_kinds": discord.KINDS,
        "masked": discord.masked,
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
    device, _ = await _visible(db, session, device_id)
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
    device, _ = await _visible(db, session, device_id)
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
    "observatory": "Was das Teleskop gerade aufnimmt (N.I.N.A.: Ziel, Sequenz, letztes Bild)",
    "aircraft": "Flugzeuge über dem aktuellen Bild (Daten: adsb.lol)",
}


@router.post("/cameras/{device_id}/public/visibility")
async def public_visibility(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """The two switches from the camera page: on the camera map, on the start page."""
    device = await _own_device(db, session, device_id)
    form = await request.form()
    profile = public.profile(device).model_copy(
        update={"show_coordinates": form.get("show_coordinates") == "on"}
    )
    device.public_profile = profile.model_dump()
    device.public_on_home = form.get("on_home") == "on"
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}#oeffentlich", status_code=303)


@router.get("/cameras/{device_id}/public/edit")
async def public_edit(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own_device(db, session, device_id)
    return render(request, "public_edit.html", _public_edit_context(device), session=session)


def _public_edit_context(device: Device) -> dict[str, object]:
    return {"device": device, "profile": public.profile(device), "sections": PUBLIC_SECTIONS}


@router.post("/cameras/{device_id}/public/edit")
async def public_save(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """What the public page shows (devices/public.py PublicProfile)."""
    device = await _own_device(db, session, device_id)
    form = await request.form()
    data: dict[str, Any] = {key: str(form.get(key) or "") for key in PUBLIC_TEXTS}
    data["show_coordinates"] = form.get("show_coordinates") == "on"
    device.public_on_home = form.get("on_home") == "on"
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
    """SPEC §6.5 ``set_settings`` from the web UI, form from the schema (§6.9); owner and
    admins (roadmap C10)."""
    device, role = await _visible(db, session, device_id, "admin")
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    schema, _ = settings_form.schema_of(device)
    diff, error = settings_form.changes(schema, settings_form.current_values(device), form)
    notice = None
    if error is None and not diff:
        # Nothing differs from what the camera reports now (e.g. the page was older than
        # its last status): no error, the page shows the current values again.
        notice = "Nichts zu speichern: Die Kamera hat diese Einstellungen schon."
        form = {}
    elif error is None:
        outcome = await camera_settings.apply(
            _registry(request),
            device.id,
            diff,
            settings_form.field_labels(schema),
            owner=role == "owner",
        )
        error = outcome.message
        if outcome.ok:
            notice = (
                "Gespeichert. Die Kamera startet die Aufnahme neu und meldet sich gleich wieder."
            )
    context = await _camera_context(
        request, db, device, form, role=role, error=error, notice=notice
    )
    return render(
        request, "camera.html", context, session=session, status_code=200 if error is None else 400
    )


@router.post("/cameras/{device_id}/members")
async def share(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    email: Annotated[str, Form(max_length=254)],
    role: Annotated[str, Form(max_length=10)] = "viewer",
) -> Response:
    """Roadmap C10: share the camera with an existing account (owner only)."""
    device = await _own_device(db, session, device_id)
    try:
        await sharing.add(db, device, email, role)
    except sharing.SharingError as exc:
        # Nothing was written; no rollback (it would expire the loaded device).
        context = await _camera_context(request, db, device, error=str(exc))
        return render(request, "camera.html", context, session=session, status_code=400)
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}#teilen", status_code=303)


@router.post("/cameras/{device_id}/members/{user_id}/remove")
async def unshare(
    db: DbSession, session: CurrentSession, device_id: str, user_id: uuid.UUID
) -> Response:
    """The owner removes someone, or a member leaves the camera."""
    if user_id == session.user.id:
        device, _ = await _visible(db, session, device_id)
        await sharing.remove(db, device.id, user_id)
        await db.commit()
        return RedirectResponse("/", status_code=303)
    device = await _own_device(db, session, device_id)
    await sharing.remove(db, device.id, user_id)
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}#teilen", status_code=303)


@router.post("/cameras/{device_id}/public/domain")
async def public_domain(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    settings: SettingsDep,
    device_id: str,
    domain: Annotated[str, Form(max_length=300)],
) -> Response:
    """Roadmap #17: the public page under the owner's own domain."""
    device = await _own_device(db, session, device_id)
    try:
        await domains.request(db, device, domain, settings.base_url, settings.data_dir)
    except domains.DomainError as exc:
        return render(
            request,
            "public_edit.html",
            {**_public_edit_context(device), "domain_error": str(exc), "domain_value": domain},
            session=session,
            status_code=400,
        )
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}/public/edit#domain", status_code=303)


@router.post("/cameras/{device_id}/public/domain/remove")
async def public_domain_remove(
    db: DbSession, session: CurrentSession, settings: SettingsDep, device_id: str
) -> Response:
    device = await _own_device(db, session, device_id)
    domains.remove(device, settings.data_dir)
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}/public/edit#domain", status_code=303)


@router.post("/cameras/{device_id}/network")
async def network_save(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """Audit L2: the owner's consent to the network of other owners' cameras."""
    device = await _own_device(db, session, device_id)
    device.network = (await request.form()).get("network") == "on"
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}#netz", status_code=303)


@router.post("/cameras/{device_id}/discord")
async def discord_save(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    """Roadmap H5: the webhook and what goes to the channel; an empty address keeps the
    stored one."""
    device = await _own_device(db, session, device_id)
    form = await request.form()
    old = discord.settings_of(device)
    webhook = str(form.get("webhook") or "").strip() or (old.webhook if old else "")
    try:
        config = discord.DiscordSettings(
            webhook=webhook,
            kinds=[k for k in discord.KINDS if form.get(f"kind_{k}") == "on"],  # pyright: ignore[reportArgumentType]
            summary=form.get("summary") == "on",
        )
    except ValidationError:
        context = await _camera_context(request, db, device, error=discord.BAD_WEBHOOK)
        return render(request, "camera.html", context, session=session, status_code=400)
    device.discord = config.model_dump()
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}#discord", status_code=303)


@router.post("/cameras/{device_id}/discord/test")
async def discord_test(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own_device(db, session, device_id)
    try:
        await ratelimit.hit(
            request.app.state.engine, f"discord-test:{device.id}", ratelimit.DISCORD_TEST
        )
        poster: discord.Discord = request.app.state.notifier.discord
        result = await poster.test(device)
    except ratelimit.RateLimitedError:
        result = "limited"
    notice = error = None
    if result == "limited":
        error = "Bitte ein paar Minuten warten, bevor du noch eine Testnachricht schickst."
    elif result == "ok":
        notice = "Testnachricht gesendet. Schau in deinen Discord-Kanal."
    elif result == "gone":
        error = "Discord kennt diesen Webhook nicht (mehr). Bitte eine neue Adresse eintragen."
    else:
        error = "Discord war nicht erreichbar. Bitte später noch einmal versuchen."
    context = await _camera_context(request, db, device, error=error, notice=notice)
    return render(request, "camera.html", context, session=session)


@router.post("/cameras/{device_id}/discord/remove")
async def discord_remove(db: DbSession, session: CurrentSession, device_id: str) -> Response:
    device = await _own_device(db, session, device_id)
    device.discord = None
    await db.commit()
    return RedirectResponse(f"/cameras/{device.id}#discord", status_code=303)
