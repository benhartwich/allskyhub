"""App API for fireballs sent later (SPEC §10.8): the same steps as the web pages
(web/routes_late.py), for the owner of a station or camera. The work and its checks are in
devices/late_fireballs.py, the words for a refusal or a failed check in web/routes_late.py.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from allskyhub_protocol import EventVariant
from allskyhub_server.api.app import AppUser
from allskyhub_server.devices import late_fireballs, meteor_pairs, queries, series
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.devices.late_fireballs import LateError
from allskyhub_server.models import Device, EventRecord, LateFireball, User
from allskyhub_server.web.deps import DbSession
from allskyhub_server.web.routes_late import reason_text, refused

router = APIRouter(prefix="/api/v1")


class LatePair(BaseModel):
    id: uuid.UUID
    other: str  # the other camera's name
    high_km: float
    low_km: float


class LateItem(BaseModel):
    id: uuid.UUID
    # "track" (waits for its track), "checking", "accepted", "failed"
    state: str
    reason: str | None = None  # late_fireballs.REASONS
    reason_text: str | None = None
    taken_at: dt.datetime  # mid-exposure, UTC
    local: str  # the same in the camera's zone, "2026-11-03T21:45:12"
    exposure_s: float
    width: int
    height: int
    track: list[list[float]]
    stars: int | None = None
    offset_deg: float | None = None
    peak: float | None = None
    # "confirmed" when the moon showed the date right (SPEC §10.8), else None
    moon: str | None = None
    orientation_until: dt.datetime | None = None  # matched an orientation from before
    hidden: bool = False
    pairs: list[LatePair] = Field(default_factory=list[LatePair])
    created_at: dt.datetime


class LateList(BaseModel):
    # The frame sizes an upload may have, the current orientation's first; none: the
    # camera has no orientation yet and cannot send any.
    sizes: list[list[int]]
    zone: str  # the camera's time zone, the default for the time
    per_day: int
    uploads: list[LateItem]


class TrackIn(BaseModel):
    track: list[list[float]] = Field(max_length=late_fireballs.MAX_TRACK)


class TimeIn(BaseModel):
    taken: str = Field(max_length=32)  # local, no offset
    zone: str = Field(max_length=64)
    exposure_s: float


async def _own(db: DbSession, user: User, device_id: str) -> Device:
    device = await queries.owned_one(db, user.id, device_id)
    if device is None:
        raise HTTPException(404, "Not found")
    return device


async def _upload(db: DbSession, device: Device, upload_id: uuid.UUID) -> LateFireball:
    upload = await db.get(LateFireball, upload_id)
    if upload is None or upload.device_id != device.id:
        raise HTTPException(404, "Not found")
    return upload


def _refuse(exc: LateError) -> HTTPException:
    status = 429 if exc.what in ("today", "checks_today") else 400
    return HTTPException(status, refused(exc.what))


async def _item(db: DbSession, device: Device, upload: LateFireball) -> LateItem:
    check: dict[str, Any] = upload.check or {}
    pairs = await meteor_pairs.for_event(db, device.id, upload.event_id) if upload.event_id else []
    until = check.get("orientation_until")

    def number(key: str) -> float | None:
        value = check.get(key)
        return float(value) if isinstance(value, int | float) else None

    stars = check.get("stars")
    return LateItem(
        id=upload.id,
        state=upload.state,
        reason=upload.reason,
        reason_text=reason_text(upload.reason, check) if upload.reason else None,
        taken_at=upload.taken_at,
        local=upload.taken_at.astimezone(series.zone(device)).replace(tzinfo=None).isoformat(),
        exposure_s=upload.exposure_s,
        width=upload.width,
        height=upload.height,
        track=upload.track_px,
        stars=stars if isinstance(stars, int) else None,
        offset_deg=number("offset_deg"),
        peak=number("peak"),
        moon="confirmed" if check.get("moon") == "confirmed" else None,
        orientation_until=dt.datetime.fromisoformat(until) if isinstance(until, str) else None,
        hidden=bool(check.get("hidden")),
        pairs=[
            LatePair(id=p.id, other=other.name, high_km=p.high_km, low_km=p.low_km)
            for p, other in pairs
        ],
        created_at=upload.created_at,
    )


@router.get("/devices/{device_id}/late")
async def list_late(db: DbSession, user: AppUser, device_id: str) -> LateList:
    device = await _own(db, user, device_id)
    rows = await db.scalars(
        select(LateFireball)
        .where(LateFireball.device_id == device.id)
        .order_by(LateFireball.created_at.desc())
        .limit(200)
    )
    return LateList(
        sizes=[[w, h] for w, h in await late_fireballs.frame_sizes(db, device)],
        zone=str(series.zone(device)),
        per_day=late_fireballs.PER_DAY,
        uploads=[await _item(db, device, u) for u in rows.all()],
    )


@router.post("/devices/{device_id}/late", status_code=201)
async def send_late(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    image: Annotated[UploadFile, File()],
    taken: Annotated[str, Form(max_length=32)],
    zone: Annotated[str, Form(max_length=64)],
    exposure_s: Annotated[float, Form()],
) -> LateItem:
    """Step 1: the frame and its time; the track follows."""
    device = await _own(db, user, device_id)
    try:
        upload = await late_fireballs.create(
            db,
            request.app.state.engine,
            request.app.state.images,
            device,
            await image.read(late_fireballs.MAX_BYTES + 1),
            late_fireballs.local_time(taken, zone),
            exposure_s,
            dt.datetime.now(dt.UTC),
        )
    except LateError as exc:
        raise _refuse(exc) from None
    return await _item(db, device, upload)


@router.get("/devices/{device_id}/late/{upload_id}")
async def get_late(db: DbSession, user: AppUser, device_id: str, upload_id: uuid.UUID) -> LateItem:
    device = await _own(db, user, device_id)
    return await _item(db, device, await _upload(db, device, upload_id))


@router.get("/devices/{device_id}/late/{upload_id}/image.jpg")
async def late_image(
    request: Request, db: DbSession, user: AppUser, device_id: str, upload_id: uuid.UUID
) -> Response:
    device = await _own(db, user, device_id)
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
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})


@router.post("/devices/{device_id}/late/{upload_id}/track")
async def set_track(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    upload_id: uuid.UUID,
    body: TrackIn,
) -> LateItem:
    """Step 2: the track; the check starts in the background (``state`` "checking")."""
    device = await _own(db, user, device_id)
    upload = await _upload(db, device, upload_id)
    if upload.state not in ("track", "failed"):
        raise HTTPException(409, "Already checked")
    state = request.app.state
    try:
        upload.track_px = late_fireballs.check_track(body.track, upload.width, upload.height)
        await late_fireballs.recheck(
            db, state.engine, state.sessionmaker, state.images, device, upload
        )
    except LateError as exc:
        raise _refuse(exc) from None
    return await _item(db, device, upload)


@router.post("/devices/{device_id}/late/{upload_id}/time")
async def set_time(
    request: Request,
    db: DbSession,
    user: AppUser,
    device_id: str,
    upload_id: uuid.UUID,
    body: TimeIn,
) -> LateItem:
    """A failed one with another time, checked again."""
    device = await _own(db, user, device_id)
    upload = await _upload(db, device, upload_id)
    if upload.state != "failed" or not upload.track_px:
        raise HTTPException(409, "Not failed")
    state = request.app.state
    try:
        at = late_fireballs.local_time(body.taken, body.zone)
        late_fireballs.check_time(at, body.exposure_s, dt.datetime.now(dt.UTC))
        await late_fireballs.recheck(
            db, state.engine, state.sessionmaker, state.images, device, upload, at, body.exposure_s
        )
    except LateError as exc:
        raise _refuse(exc) from None
    return await _item(db, device, upload)


@router.delete("/devices/{device_id}/late/{upload_id}", status_code=204)
async def delete_late(
    request: Request, db: DbSession, user: AppUser, device_id: str, upload_id: uuid.UUID
) -> Response:
    """The owner deletes one, and with it every pair it made (SPEC §10.8)."""
    device = await _own(db, user, device_id)
    upload = await _upload(db, device, upload_id)
    await late_fireballs.remove(db, request.app.state.images, upload)
    await db.commit()
    return Response(status_code=204)
