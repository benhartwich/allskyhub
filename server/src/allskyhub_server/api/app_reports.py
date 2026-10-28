"""Reporting the sky brightness by eye from the app (Benjamin, 2026-10-28): the chart to
compare with, the report itself, and the reporter's own count.

Only signed-in accounts report (reporter accounts from /melden/konto, or camera owners).
A report is taken only at astronomical night at the place, with the constellation at least
``report.MIN_ALTITUDE`` high, at most one per account and hour; the place is stored
rounded to 0.1°. The estimate of the sky brightness follows from the faintest stars seen
(allskyhub_sky.report), as rough as the method.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from allskyhub_server.api.app import AppUser, _In  # pyright: ignore[reportPrivateUsage]
from allskyhub_server.i18n import english, tr
from allskyhub_server.models import SkyReport
from allskyhub_server.web.deps import DbSession
from allskyhub_sky import alt_az, constellations, precess, report, sidereal_deg
from allskyhub_sky.astro import moon_altitude, moon_illumination, sun_altitude

router = APIRouter(prefix="/api/v1/reports")

NIGHT = -18.0  # sun altitude: astronomical night
EVERY = dt.timedelta(hours=1)


def _now() -> dt.datetime:
    """The current time (a function, so tests can put it at night)."""
    return dt.datetime.now(dt.UTC)


Lat = Annotated[float, Query(ge=-90, le=90)]
Lon = Annotated[float, Query(ge=-180, le=180)]


class ReportChart(BaseModel):
    ready: bool
    # Why not now (German or English), when not ready.
    reason: str | None = None
    key: str | None = None
    name: str | None = None
    altitude: float | None = None
    azimuth: float | None = None
    # [x, y, magnitude]: degrees on the tangent plane, y toward the zenith.
    stars: list[tuple[float, float, float]] = []
    limits: list[float] = list(report.CHART_LIMITS)
    sun_altitude: float
    moon_up: bool
    moon_illumination: float
    next_at: dt.datetime | None = None


class ReportIn(_In):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    constellation: str = Field(max_length=3)
    limit_mag: float = Field(ge=1, le=7.5)
    clouds: bool = False


class ReportOut(BaseModel):
    sqm: float
    label: str
    limit_mag: float
    next_at: dt.datetime


class Mine(BaseModel):
    count: int
    last_sqm: float | None
    last_at: dt.datetime | None
    next_at: dt.datetime | None


async def _next_at(db: DbSession, user: AppUser, now: dt.datetime) -> dt.datetime | None:
    last = await db.scalar(select(func.max(SkyReport.at)).where(SkyReport.user_id == user.id))
    return last + EVERY if last is not None and last + EVERY > now else None


@router.get("/chart")
async def chart(db: DbSession, user: AppUser, lat: Lat, lon: Lon) -> ReportChart:
    lat, lon = round(lat, 1), round(lon, 1)
    now = _now()
    sun = sun_altitude(now, lat, lon)
    moon_up = moon_altitude(now, lat, lon) > 0
    sky = ReportChart(
        ready=False,
        sun_altitude=round(sun, 1),
        moon_up=moon_up,
        moon_illumination=round(moon_illumination(now), 2),
        next_at=await _next_at(db, user, now),
    )
    if sun > NIGHT:
        return sky.model_copy(
            update={
                "reason": tr(
                    "Es ist noch nicht dunkel genug. Melden geht, wenn die Sonne mindestens 18° "
                    "unter dem Horizont steht.",
                    "It is not dark enough yet. Reporting works once the sun is at least 18° "
                    "below the horizon.",
                )
            }
        )
    found = report.chart(lat, lon, now, latin=english())
    if found is None:
        return sky.model_copy(
            update={
                "reason": tr(
                    "Gerade steht keines der Sternbilder hoch genug. Später noch einmal.",
                    "None of the constellations is high enough right now. Try again later.",
                )
            }
        )
    return sky.model_copy(
        update={
            "ready": True,
            "key": found.key,
            "name": found.name,
            "altitude": found.altitude,
            "azimuth": found.azimuth,
            "stars": found.stars,
        }
    )


@router.post("")
async def submit(request: Request, body: ReportIn, db: DbSession, user: AppUser) -> ReportOut:
    lat, lon = round(body.lat, 1), round(body.lon, 1)
    now = _now()
    if (next_at := await _next_at(db, user, now)) is not None:
        raise HTTPException(
            429,
            tr(
                f"Die nächste Meldung geht ab {next_at.strftime('%H:%M')} UTC.",
                f"The next report is possible from {next_at.strftime('%H:%M')} UTC.",
            ),
        )
    if sun_altitude(now, lat, lon) > NIGHT:
        raise HTTPException(
            409, tr("Es ist noch nicht dunkel genug.", "It is not dark enough yet.")
        )
    if body.limit_mag not in report.CHART_LIMITS:
        raise HTTPException(422, "limit_mag")
    found = constellations().get(body.constellation)
    if body.constellation not in report.ORDER or found is None:
        raise HTTPException(422, "constellation")
    _, ra, dec = found
    altitude = alt_az(*precess(ra, dec, now), sidereal_deg(now, lon), lat)[0]
    if altitude < report.MIN_ALTITUDE - 5:  # a little slack for the minutes spent looking
        raise HTTPException(
            409,
            tr(
                "Das Sternbild steht hier gerade zu tief für eine Meldung.",
                "The constellation is too low here for a report right now.",
            ),
        )
    sqm = report.sqm_from_limit(body.limit_mag)
    db.add(
        SkyReport(
            user_id=user.id,
            at=now,
            lat=lat,
            lon=lon,
            constellation=body.constellation,
            limit_mag=body.limit_mag,
            sqm=sqm,
            clouds=body.clouds,
        )
    )
    await db.commit()
    return ReportOut(
        sqm=sqm,
        label=report.sky_class(sqm, english=english()),
        limit_mag=body.limit_mag,
        next_at=now + EVERY,
    )


@router.get("/mine")
async def mine(db: DbSession, user: AppUser) -> Mine:
    now = _now()
    count = await db.scalar(select(func.count()).where(SkyReport.user_id == user.id)) or 0
    last = await db.scalar(
        select(SkyReport).where(SkyReport.user_id == user.id).order_by(SkyReport.at.desc())
    )
    return Mine(
        count=count,
        last_sqm=last.sqm if last else None,
        last_at=last.at if last else None,
        next_at=await _next_at(db, user, now),
    )
