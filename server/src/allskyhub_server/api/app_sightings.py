"""Sightings by eye from the app (stage 5) and the alerts at a chosen place.

Reporting needs an account (like the sky brightness); the place is stored rounded to
0.1°. Aurora reports, with photos only once approved, are public to read; a fireball
report's matches only to its reporter. The app's alert place, radius and switches live
on the account (push: push/notify.py).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from allskyhub_server.api import app_sky
from allskyhub_server.api.app import AppUser, _In  # pyright: ignore[reportPrivateUsage]
from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import sightings, tonight
from allskyhub_server.i18n import tr
from allskyhub_server.models import AuroraFlag, AuroraReport, FireballReport
from allskyhub_server.settings import Settings
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip

router = APIRouter(prefix="/api/v1")

FIREBALLS_PER_DAY = 10
AURORA_EVERY = dt.timedelta(minutes=30)
AURORA_HOURS = 6
AURORA_LIST_KM = 1500.0
RADII = (100, 300, 500)

Lat = Annotated[float, Query(ge=-90, le=90)]
Lon = Annotated[float, Query(ge=-180, le=180)]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _photos(request: Request) -> Path:
    settings: Settings = request.app.state.settings
    return settings.data_dir / "sightings"


class FireballIn(_In):
    seen_at: dt.datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    start: tuple[float, float] | None = None  # altitude, azimuth
    end: tuple[float, float] | None = None
    brightness: Literal["star", "venus", "moon", "day"]
    details: list[str] = Field(default=[], max_length=8)


class CameraHit(BaseModel):
    at: dt.datetime
    title: str | None
    image: str | None


class FireballOut(BaseModel):
    id: uuid.UUID
    cameras: list[CameraHit]
    # Measured tracks: /feuerkugeln/<id> on the hub.
    tracks: list[str]
    others_km: list[float]


@router.post("/sightings/fireball")
async def fireball(body: FireballIn, db: DbSession, user: AppUser) -> FireballOut:
    now = _now()
    seen = body.seen_at if body.seen_at.tzinfo else body.seen_at.replace(tzinfo=dt.UTC)
    if not now - dt.timedelta(hours=24) <= seen <= now + dt.timedelta(minutes=5):
        raise HTTPException(
            422, tr("Bitte nur Sichtungen der letzten 24 Stunden.", "Only the last 24 hours.")
        )
    today = await db.scalar(
        select(func.count()).where(
            FireballReport.user_id == user.id,
            FireballReport.created_at >= now - dt.timedelta(days=1),
        )
    )
    if (today or 0) >= FIREBALLS_PER_DAY:
        raise HTTPException(429, tr("Zu viele Meldungen heute.", "Too many reports today."))
    for ends in (body.start, body.end):
        if ends is not None and not (-5 <= ends[0] <= 90 and 0 <= ends[1] <= 360):
            raise HTTPException(422, "direction")
    report = FireballReport(
        user_id=user.id,
        seen_at=seen,
        lat=round(body.lat, 1),
        lon=round(body.lon, 1),
        start_alt=body.start[0] if body.start else None,
        start_az=body.start[1] if body.start else None,
        end_alt=body.end[0] if body.end else None,
        end_az=body.end[1] if body.end else None,
        brightness=body.brightness,
        details=[d for d in body.details if d in sightings.FIREBALL_DETAILS],
    )
    db.add(report)
    await db.commit()
    return await _fireball_out(db, report)


async def _fireball_out(db: DbSession, report: FireballReport) -> FireballOut:
    found = await sightings.match(db, report)
    return FireballOut(
        id=report.id,
        cameras=[CameraHit(at=c.at, title=c.title, image=c.image) for c in found.cameras],
        tracks=[f"/feuerkugeln/{t}" for t in found.tracks],
        others_km=found.others,
    )


@router.get("/sightings/fireball/{report_id}")
async def fireball_again(report_id: uuid.UUID, db: DbSession, user: AppUser) -> FireballOut:
    """The matches later on (cameras upload their detections with a delay)."""
    report = await db.get(FireballReport, report_id)
    if report is None or report.user_id != user.id:
        raise HTTPException(404)
    return await _fireball_out(db, report)


class AuroraOut(BaseModel):
    id: uuid.UUID
    at: dt.datetime
    distance_km: float
    naked_eye: bool
    details: list[str]
    photo: str | None  # on the hub, only once approved
    thumb: str | None
    author: str | None
    # Your own report's photo, while it waits: "pending", "approved", "rejected", "none".
    photo_state: str | None = None


def _aurora_out(r: AuroraReport, km: float, *, own: bool = False) -> AuroraOut:
    shown = r.photo == "approved"
    return AuroraOut(
        id=r.id,
        at=r.seen_at,
        # In steps of 25 km ("up to 25 km"), never exact (review of #330).
        distance_km=0 if own else sightings.step_km(km, sightings.AURORA_STEP_KM),
        naked_eye=r.naked_eye,
        details=r.details,
        photo=f"/api/v1/sightings/aurora/{r.id}/photo.jpg" if shown else None,
        thumb=f"/api/v1/sightings/aurora/{r.id}/photo.jpg?thumb=1" if shown else None,
        author=(r.author or tr("anonym", "anonymous")) if shown else None,
        photo_state=r.photo if own else None,
    )


@router.post("/sightings/aurora")
async def aurora(
    request: Request,
    db: DbSession,
    user: AppUser,
    lat: Annotated[float, Form(ge=-90, le=90)],
    lon: Annotated[float, Form(ge=-180, le=180)],
    naked_eye: Annotated[bool, Form()],
    details: Annotated[str, Form(max_length=200)] = "",
    author: Annotated[str, Form(max_length=100)] = "",
    licence: Annotated[bool, Form()] = False,
    photo: Annotated[UploadFile | None, File()] = None,
) -> AuroraOut:
    now = _now()
    last = await db.scalar(
        select(func.max(AuroraReport.created_at)).where(AuroraReport.user_id == user.id)
    )
    if last is not None and now - last < AURORA_EVERY:
        raise HTTPException(
            429, tr("Die nächste Meldung geht in einer halben Stunde.", "Next report in 30 min.")
        )
    if photo is not None and not licence:
        raise HTTPException(
            422,
            tr(
                "Für ein Foto bitte der Lizenz CC BY 4.0 zustimmen.",
                "For a photo, please agree to the CC BY 4.0 licence.",
            ),
        )
    report = AuroraReport(
        user_id=user.id,
        seen_at=now,
        lat=round(lat, 1),
        lon=round(lon, 1),
        naked_eye=naked_eye,
        details=[d for d in details.split(",") if d in sightings.AURORA_DETAILS],
        author=" ".join(author.split())[:100],
    )
    db.add(report)
    await db.flush()
    if photo is not None:
        data = await photo.read(sightings.PHOTO_MAX_UPLOAD + 1)
        try:
            await asyncio.to_thread(sightings.store_photo, _photos(request), report.id, data)
        except sightings.PhotoError:
            await db.rollback()
            raise HTTPException(
                422, tr("Das Foto konnte nicht gelesen werden.", "Could not read the photo.")
            ) from None
        report.photo = "pending"
    await db.commit()
    notifier = request.app.state.notifier
    asyncio.create_task(notifier.aurora_near(report.id))  # noqa: RUF006
    if report.photo == "pending":
        asyncio.create_task(notifier.photo_to_review(report.id))  # noqa: RUF006
    return _aurora_out(report, 0.0, own=True)


@router.get("/sightings/aurora")
async def aurora_list(
    request: Request, db: DbSession, settings: SettingsDep, lat: Lat, lon: Lon
) -> list[AuroraOut]:
    """Aurora reports of the last hours, nearest first; limited per IP like the sky."""
    try:
        await ratelimit.hit(
            request.app.state.engine,
            f"public-sky:{client_ip(request, settings)}",
            app_sky.SKY_PER_IP,
        )
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Too many requests", {"Retry-After": str(exc.retry_after)}
        ) from None
    since = _now() - dt.timedelta(hours=AURORA_HOURS)
    found = await sightings.aurora_near(db, (round(lat, 1), round(lon, 1)), since, AURORA_LIST_KM)
    return [_aurora_out(r, km) for r, km in found[:50]]


@router.get("/sightings/aurora/{report_id}/photo.jpg")
async def aurora_photo(
    request: Request, report_id: uuid.UUID, db: DbSession, thumb: bool = False
) -> Response:
    report = await db.get(AuroraReport, report_id)
    if report is None or report.photo != "approved":
        raise HTTPException(404)
    path = sightings.photo_path(_photos(request), report.id, thumb=thumb)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"}
    )


@router.post("/sightings/aurora/{report_id}/flag", status_code=204)
async def flag(report_id: uuid.UUID, db: DbSession, user: AppUser) -> None:
    """Report a photo as unsuitable; three flags send it back to the admins."""
    report = await db.get(AuroraReport, report_id)
    if report is None or report.photo != "approved":
        raise HTTPException(404)
    try:
        async with db.begin_nested():
            db.add(AuroraFlag(report_id=report.id, user_id=user.id))
    except IntegrityError:
        return  # flagged before by this account
    report.flags += 1
    if report.flags >= sightings.FLAGS_TO_REVIEW:
        report.photo = "pending"
    await db.commit()


class Alerts(_In):
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    radius_km: int | None = None
    aurora: bool | None = None
    fireball: bool | None = None
    clear_night: bool | None = None
    # The phone's time zone (IANA), for the evening notice's times.
    tz: str | None = Field(default=None, max_length=64)


@router.get("/account/alerts")
async def alerts(user: AppUser) -> Alerts:
    return Alerts(
        lat=user.alert_lat,
        lon=user.alert_lon,
        radius_km=user.alert_radius_km,
        aurora=user.notify_aurora_near,
        fireball=user.notify_fireball_near,
        clear_night=user.notify_clear_here,
        tz=user.alert_tz,
    )


@router.put("/account/alerts")
async def put_alerts(body: Alerts, db: DbSession, user: AppUser) -> Alerts:
    """Only the given fields change. The place is kept rounded to 0.1°; switching all
    alerts off forgets it."""
    if body.radius_km is not None:
        if body.radius_km not in RADII:
            raise HTTPException(422, "radius_km")
        user.alert_radius_km = body.radius_km
    if body.lat is not None and body.lon is not None:
        user.alert_lat, user.alert_lon = round(body.lat, 1), round(body.lon, 1)
    if body.tz is not None:
        user.alert_tz = tonight.zone_for(body.tz).key
    if body.aurora is not None:
        user.notify_aurora_near = body.aurora
    if body.fireball is not None:
        user.notify_fireball_near = body.fireball
    if body.clear_night is not None:
        user.notify_clear_here = body.clear_night
    if not (user.notify_aurora_near or user.notify_fireball_near or user.notify_clear_here):
        user.alert_lat = user.alert_lon = user.alert_tz = None
    await db.commit()
    return await alerts(user)
