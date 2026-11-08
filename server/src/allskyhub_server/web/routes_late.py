"""Fireballs sent later (SPEC §10.8): the owner's pages under /nachreichen/<camera> and the
admin's list to hide one. The check itself runs in the background
(devices/late_fireballs.py); a page that waits for it reloads itself.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
import zoneinfo
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_protocol import EventVariant
from allskyhub_server.auth.sessions import SessionInfo
from allskyhub_server.devices import late_fireballs, meteor_pairs, queries, series
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.late_fireballs import LateError
from allskyhub_server.i18n import tr
from allskyhub_server.models import Device, EventRecord, LateFireball, User
from allskyhub_server.web import flash
from allskyhub_server.web.deps import CurrentSession, DbSession, SettingsDep, csrf_protect
from allskyhub_server.web.render import render
from allskyhub_server.web.routes_admin import admin_only

router = APIRouter(dependencies=[Depends(csrf_protect)])

ZONES = sorted(zoneinfo.available_timezones() - {"Factory", "localtime"})


def _refused(what: str) -> str:
    """The words for an upload refused at once (LateError)."""
    words = {
        "too_large": tr("Das Bild ist größer als 8 MB.", "The image is larger than 8 MB."),
        "image": tr("Bitte ein JPEG oder PNG.", "Please a JPEG or PNG."),
        "size": tr(
            "Das Bild hat nicht die Größe der Kamera.", "The image does not have the camera's size."
        ),
        "exposure": tr(
            "Die Belichtung muss zwischen 0 und 120 s liegen.",
            "The exposure must be between 0 and 120 s.",
        ),
        "age": tr(
            "Bitte eine Aufnahme aus den letzten 12 Monaten.",
            "Please a recording of the last 12 months.",
        ),
        "time": tr("Datum oder Zeitzone stimmen nicht.", "Date or time zone are not valid."),
        "track": tr(
            "Bitte 2 bis 32 Punkte auf der Spur setzen.", "Please set 2 to 32 points on the track."
        ),
        "today": tr(
            "Heute gingen schon 20 Aufnahmen ein. Morgen geht es weiter.",
            "20 recordings came in today already. More tomorrow.",
        ),
        "no_orientation": tr(
            "Die Kamera hat noch keine Ausrichtung. Sie kommt mit der ersten klaren Nacht.",
            "The camera has no orientation yet. It comes with the first clear night.",
        ),
    }
    return words.get(what, what)


def reason_text(reason: str | None, check: dict[str, Any]) -> str:
    """Why a check failed, for the owner (late_fireballs.REASONS)."""
    off = check.get("offset_deg")
    turned = tr(
        "Die Sterne passen, aber der Himmel liegt "
        + (f"{off:.1f}".replace(".", ",") + "° " if isinstance(off, int | float) else "")
        + "neben der Ausrichtung der Kamera. Entweder stimmt die Uhrzeit nicht (2 Minuten "
        "sind etwa 0,5°) oder die Kamera wurde seither bewegt.",
        "The stars fit, but the sky lies "
        + (f"{off:.1f}° " if isinstance(off, int | float) else "")
        + "away from the camera's orientation. Either the time is wrong (2 minutes are "
        "about 0.5°) or the camera was moved since.",
    )
    words = {
        "no_orientation": _refused("no_orientation"),
        "size": tr(
            "Die Kamera liefert inzwischen Bilder anderer Größe.",
            "The camera sends images of another size by now.",
        ),
        "daylight": tr(
            "Zur angegebenen Zeit war es dort nicht dunkel (Sonne über -18°). Stimmt die Zeitzone?",
            "At the given time it was not dark there (sun above -18°). Is the time zone right?",
        ),
        "few_stars": tr(
            "Zu wenige Sterne im Bild: bewölkt oder zu hell.",
            "Too few stars in the image: cloudy or too bright.",
        ),
        "no_match": tr(
            "Die Sterne passen nicht zu Ort und Zeit. Stimmen Datum, Uhrzeit und Zeitzone?",
            "The stars do not fit the place and time. Are date, time and time zone right?",
        ),
        "turned": turned,
        "mirrored": tr(
            "Das Bild ist anders gespiegelt als die Bilder der Kamera.",
            "The image is mirrored differently from the camera's images.",
        ),
        "track": tr("Die Spur reicht über den Himmel hinaus.", "The track reaches beyond the sky."),
        "not_fireball": tr(
            "Das ist keine Feuerkugel: zu schwach und kürzer als 15°. Nachreichen geht nur "
            "für Feuerkugeln.",
            "This is no fireball: too faint and shorter than 15°. Only fireballs can be sent "
            "later.",
        ),
        "known": tr(
            "Die Kamera hat zu dieser Zeit schon einen Meteor.",
            "The camera has a meteor at this time already.",
        ),
    }
    return words.get(reason or "", reason or "")


async def _own(db: AsyncSession, session: SessionInfo, device_id: str) -> Device:
    device = await queries.owned_one(db, session.user.id, device_id)
    if device is None:
        raise HTTPException(404)
    return device


async def _upload(db: AsyncSession, device: Device, upload_id: uuid.UUID) -> LateFireball:
    upload = await db.get(LateFireball, upload_id)
    if upload is None or upload.device_id != device.id:
        raise HTTPException(404)
    return upload


def _back(device: Device) -> str:
    return f"/stationen/{device.id}" if device.kind == "station" else f"/cameras/{device.id}"


def _local_time(value: str, zone: str) -> dt.datetime:
    """The owner's local mid-exposure in UTC."""
    if zone not in ZONES:
        raise LateError("time")
    try:
        local = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise LateError("time") from exc
    if local.tzinfo is not None:
        raise LateError("time")
    return local.replace(tzinfo=zoneinfo.ZoneInfo(zone)).astimezone(dt.UTC)


async def _list_page(
    request: Request,
    db: AsyncSession,
    session: SessionInfo,
    device: Device,
    status_code: int = 200,
    **extra: object,
) -> Response:
    uploads = list(
        await db.scalars(
            select(LateFireball)
            .where(LateFireball.device_id == device.id)
            .order_by(LateFireball.created_at.desc())
            .limit(200)
        )
    )
    return render(
        request,
        "late_fireballs.html",
        {
            "device": device,
            "back": _back(device),
            "size": late_fireballs.frame_size(device),
            "uploads": uploads,
            "zones": ZONES,
            "zone": str(series.zone(device)),
            "reason_text": reason_text,
            **extra,
        },
        session=session,
        status_code=status_code,
    )


@router.get("/nachreichen/{device_id}")
async def list_page(
    request: Request, db: DbSession, session: CurrentSession, device_id: str
) -> Response:
    device = await _own(db, session, device_id)
    status, kept = flash.take(request, session)
    return await _list_page(request, db, session, device, status, **kept)


@router.post("/nachreichen/{device_id}")
async def send(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    image: Annotated[UploadFile, File()],
    taken: Annotated[str, Form(max_length=32)],
    zone: Annotated[str, Form(max_length=64)],
    exposure_s: Annotated[float, Form()],
) -> Response:
    """Step 1: the frame and its time; the track follows on the upload's page."""
    device = await _own(db, session, device_id)
    now = dt.datetime.now(dt.UTC)
    store: ImageStore = request.app.state.images
    form = {"taken": taken, "zone": zone, "exposure_s": exposure_s}
    try:
        size = late_fireballs.frame_size(device)
        if size is None:
            raise LateError("no_orientation")
        if await late_fireballs.sent_today(db, device, now) >= late_fireballs.PER_DAY:
            raise LateError("today")
        at = _local_time(taken, zone)
        late_fireballs.check_time(at, exposure_s, now)
        data = late_fireballs.as_jpeg(await image.read(late_fireballs.MAX_BYTES + 1))
        if await asyncio.to_thread(late_fireballs.image_size, data) != size:
            raise LateError("size")
    except LateError as exc:
        return await _list_page(
            request, db, session, device, 400, error=_refused(exc.what), form=form
        )
    upload = LateFireball(
        device_id=device.id,
        taken_at=at,
        exposure_s=exposure_s,
        track_px=[],
        width=size[0],
        height=size[1],
    )
    db.add(upload)
    await db.flush()
    await asyncio.to_thread(store.save_late, device.id, str(upload.id), data)
    await db.commit()
    return RedirectResponse(f"/nachreichen/{device.id}/{upload.id}", status_code=303)


@router.get("/nachreichen/{device_id}/{upload_id}")
async def upload_page(
    request: Request, db: DbSession, session: CurrentSession, device_id: str, upload_id: uuid.UUID
) -> Response:
    device = await _own(db, session, device_id)
    upload = await _upload(db, device, upload_id)
    status, kept = flash.take(request, session)
    pairs = await meteor_pairs.for_event(db, device.id, upload.event_id) if upload.event_id else []
    zone = series.zone(device)
    return render(
        request,
        "late_fireball.html",
        {
            "device": device,
            "back": _back(device),
            "upload": upload,
            "local": upload.taken_at.astimezone(zone),
            "zone": str(zone),
            "zones": ZONES,
            "pairs": pairs,
            "reason": reason_text(upload.reason, upload.check),
            **kept,
        },
        session=session,
        status_code=status,
    )


@router.get("/nachreichen/{device_id}/{upload_id}/bild.jpg")
async def upload_image(
    request: Request, db: DbSession, session: CurrentSession, device_id: str, upload_id: uuid.UUID
) -> Response:
    device = await _own(db, session, device_id)
    upload = await _upload(db, device, upload_id)
    store: ImageStore = request.app.state.images
    path = store.late_path(device.id, str(upload.id))
    if upload.event_id is not None:
        event = await db.scalar(
            select(EventRecord).where(
                EventRecord.device_id == device.id, EventRecord.event_id == upload.event_id
            )
        )
        if event is not None:
            path = store.event_path(
                device.id, event.night_id, event.event_id, EventVariant.ORIGINAL
            )
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})


@router.post("/nachreichen/{device_id}/{upload_id}/spur")
async def set_track(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    upload_id: uuid.UUID,
    track: Annotated[str, Form(max_length=4000)],
) -> Response:
    """Step 2: the track; the check starts."""
    device = await _own(db, session, device_id)
    upload = await _upload(db, device, upload_id)
    back = f"/nachreichen/{device.id}/{upload.id}"
    if upload.state not in ("track", "failed"):
        raise HTTPException(409)
    try:
        points: Any = json.loads(track)
        if not isinstance(points, list):
            raise LateError("track")
        upload.track_px = late_fireballs.check_track(points, upload.width, upload.height)  # pyright: ignore[reportUnknownArgumentType]
    except (ValueError, TypeError, LateError):
        return flash.redirect(request, session, back, 400, {"error": _refused("track")})
    await _recheck(request, db, upload)
    return RedirectResponse(back, status_code=303)


@router.post("/nachreichen/{device_id}/{upload_id}/zeit")
async def set_time(
    request: Request,
    db: DbSession,
    session: CurrentSession,
    device_id: str,
    upload_id: uuid.UUID,
    taken: Annotated[str, Form(max_length=32)],
    zone: Annotated[str, Form(max_length=64)],
    exposure_s: Annotated[float, Form()],
) -> Response:
    """A failed one with another time, checked again."""
    device = await _own(db, session, device_id)
    upload = await _upload(db, device, upload_id)
    back = f"/nachreichen/{device.id}/{upload.id}"
    if upload.state != "failed" or not upload.track_px:
        raise HTTPException(409)
    try:
        at = _local_time(taken, zone)
        late_fireballs.check_time(at, exposure_s, dt.datetime.now(dt.UTC))
    except LateError as exc:
        return flash.redirect(request, session, back, 400, {"error": _refused(exc.what)})
    upload.taken_at, upload.exposure_s = at, exposure_s
    await _recheck(request, db, upload)
    return RedirectResponse(back, status_code=303)


async def _recheck(request: Request, db: AsyncSession, upload: LateFireball) -> None:
    upload.state, upload.reason, upload.check = "checking", None, {}
    await db.commit()
    late_fireballs.start(request.app.state.sessionmaker, request.app.state.images, upload.id)


@router.post("/nachreichen/{device_id}/{upload_id}/loeschen")
async def remove(
    request: Request, db: DbSession, session: CurrentSession, device_id: str, upload_id: uuid.UUID
) -> Response:
    device = await _own(db, session, device_id)
    upload = await _upload(db, device, upload_id)
    await late_fireballs.remove(db, request.app.state.images, upload)
    await db.commit()
    return flash.redirect(
        request, session, f"/nachreichen/{device.id}", 200, {"notice": tr("Gelöscht.", "Deleted.")}
    )


# The admin hides one (SPEC §10.8).


@router.get("/admin/nachgereicht")
async def admin_list(
    request: Request, db: DbSession, settings: SettingsDep, session: CurrentSession
) -> Response:
    admin_only(session, settings)
    status, kept = flash.take(request, session)
    rows = (
        await db.execute(
            select(LateFireball, Device, User)
            .join(Device, Device.id == LateFireball.device_id)
            .outerjoin(User, User.id == Device.owner_id)
            .where(LateFireball.state == "accepted")
            .order_by(LateFireball.created_at.desc())
            .limit(300)
        )
    ).all()
    return render(
        request, "admin_late.html", {"rows": rows, **kept}, session=session, status_code=status
    )


@router.get("/admin/nachgereicht/{upload_id}/bild.jpg")
async def admin_image(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    session: CurrentSession,
    upload_id: uuid.UUID,
) -> Response:
    admin_only(session, settings)
    upload = await db.get(LateFireball, upload_id)
    event = (
        await db.scalar(
            select(EventRecord).where(
                EventRecord.device_id == upload.device_id, EventRecord.event_id == upload.event_id
            )
        )
        if upload is not None and upload.event_id is not None
        else None
    )
    if event is None:
        raise HTTPException(404)
    store: ImageStore = request.app.state.images
    path = store.event_path(event.device_id, event.night_id, event.event_id, EventVariant.THUMB)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})


@router.post("/admin/nachgereicht/{upload_id}/ausblenden")
async def admin_hide(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    session: CurrentSession,
    upload_id: uuid.UUID,
) -> Response:
    admin_only(session, settings)
    upload = await db.get(LateFireball, upload_id)
    if upload is None:
        raise HTTPException(404)
    await late_fireballs.hide(db, upload)
    await db.commit()
    return flash.redirect(
        request, session, "/admin/nachgereicht", 200, {"notice": tr("Ausgeblendet.", "Hidden.")}
    )
