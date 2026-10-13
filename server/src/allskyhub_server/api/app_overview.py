"""App API, what the web camera page shows beyond the archive: the camera's health, the
night ahead ("Heute Nacht", roadmap C1), last night in numbers (C3) and the measurement
series for the charts (the same data as the web viewer, devices/series.py).

Read-only, for everyone the camera is shared with (roadmap C10).
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from allskyhub_server.api.app import AppUser
from allskyhub_server.devices import health, night_summary, queries, series, target, tonight
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Device, User
from allskyhub_server.web.deps import DbSession
from allskyhub_sky.objects import catalogue

router = APIRouter(prefix="/api/v1")


async def _own(db: DbSession, user: User, device_id: str) -> Device:
    found = await queries.visible_one(db, user.id, device_id)
    if found is None:
        raise HTTPException(404, "Not found")
    return found[0]


class HealthItem(BaseModel):
    key: str
    level: Literal["warn", "error"]
    text: str


class TonightHour(BaseModel):
    at: dt.datetime
    clouds: int | None
    moon_up: bool
    good: bool


class TonightInfo(BaseModel):
    summary: str
    sunset: dt.datetime | None
    dusk: dt.datetime | None
    dawn: dt.datetime | None
    sunrise: dt.datetime | None
    moonrise: dt.datetime | None
    moonset: dt.datetime | None
    moon_illumination: float
    moon_phase: str
    best_start: dt.datetime | None
    best_end: dt.datetime | None
    hours: list[TonightHour]


class LastNight(BaseModel):
    night_id: str
    line: str
    meteors: int
    clear_hours: float | None
    best_sqm: float | None


class Overview(BaseModel):
    online: bool
    health: list[HealthItem]
    tonight: TonightInfo | None
    last_night: LastNight | None


@router.get("/cameras/{device_id}/overview")
async def overview(request: Request, db: DbSession, user: AppUser, device_id: str) -> Overview:
    device = await _own(db, user, device_id)
    registry: ConnectionRegistry = request.app.state.connections
    forecast: ForecastCache = request.app.state.forecast
    now = dt.datetime.now(dt.UTC)
    online = registry.is_online(device.id)
    problems = await health.full_check(db, device, online, now, forecast)
    night = await tonight.for_device(forecast, device.last_status, now)
    info = None
    if night is not None:
        n = night.night
        info = TonightInfo(
            summary=night.summary,
            sunset=n.sunset,
            dusk=n.dusk,
            dawn=n.dawn,
            sunrise=n.sunrise,
            moonrise=n.moonrise,
            moonset=n.moonset,
            moon_illumination=n.moon_illumination,
            moon_phase=night.moon_phase,
            best_start=night.best[0] if night.best else None,
            best_end=night.best[1] if night.best else None,
            hours=[
                TonightHour(at=h.at, clouds=h.clouds, moon_up=h.moon_up, good=h.good)
                for h in night.hours
            ],
        )
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    night_id = night_summary.last_night_id(now, tonight.zone_for(settings.get("timezone")))
    summary = await night_summary.summarize(db, device.id, night_id)
    last = None
    if not summary.empty:
        last = LastNight(
            night_id=night_id,
            line=summary.line(),
            meteors=summary.meteor_count,
            clear_hours=summary.clear_hours,
            best_sqm=summary.best_sqm,
        )
    return Overview(
        online=online,
        health=[HealthItem(key=p.key, level=p.level, text=p.text) for p in problems],
        tonight=info,
        last_night=last,
    )


@router.get("/cameras/{device_id}/series")
async def measurement_series(
    db: DbSession,
    user: AppUser,
    device_id: str,
    night: Annotated[str | None, Query(pattern=r"^\d{8}$")] = None,
    hours: int | None = None,
) -> dict[str, Any]:
    """The charts' data: as GET /cameras/<id>/charts/data on the web (devices/series.py)."""
    device = await _own(db, user, device_id)
    rng = await series.resolve(db, device, night, hours, dt.datetime.now(dt.UTC))
    return await series.load(db, device, rng, with_status=True, public=False)


class TargetItem(BaseModel):
    key: str
    label: str
    kind: str
    mag: float | None


class TargetHourItem(BaseModel):
    at: dt.datetime
    alt: float
    az: float
    direction: str
    clouds: int | None
    moon_near: bool
    # clear, partly, cloudy, low (below 20°), bright (sun above -12°)
    verdict: str
    verdict_text: str


class TargetPlan(BaseModel):
    """Roadmap H4. ``now.cloud_cover`` is the camera's own value (0..1) or null."""

    key: str
    label: str
    summary: str
    tz: str
    alt: float
    az: float
    direction: str
    cloud_cover: float | None
    # Where cloud_cover comes from: "in Richtung des Objekts" (the camera's cloud map) or
    # "ganzer Himmel".
    cloud_source: str
    verdict: str
    verdict_text: str
    best_start: dt.datetime | None
    best_end: dt.datetime | None
    hours: list[TargetHourItem]


@router.get("/targets")
async def targets(user: AppUser) -> list[TargetItem]:
    """The objects to choose from: planets, then M1 … M110."""
    return [TargetItem(key=o.key, label=o.label, kind=o.kind, mag=o.mag) for o in catalogue()]


@router.get("/cameras/{device_id}/target")
async def target_plan(
    request: Request, db: DbSession, user: AppUser, device_id: str, key: str = Query(max_length=20)
) -> TargetPlan:
    """404 for an unknown object, 409 when the camera's location is unknown."""
    device = await _own(db, user, device_id)
    chosen = target.find(key)
    if chosen is None:
        raise HTTPException(404, "Dieses Objekt kenne ich nicht.")
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    hours: list[tonight.Hour] = []
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        forecast: ForecastCache = request.app.state.forecast
        hours = await forecast.get(float(lat), float(lon))
    found = target.plan(chosen, device.last_status, hours, dt.datetime.now(dt.UTC))
    if found is None:
        raise HTTPException(409, "Die Kamera kennt ihren Standort noch nicht.")
    best = found.best
    return TargetPlan(
        key=chosen.key,
        label=chosen.label,
        summary=found.summary(),
        tz=found.tz,
        alt=round(found.now.alt, 1),
        az=round(found.now.az, 1),
        direction=found.now.direction,
        cloud_cover=found.now.cloud_cover,
        cloud_source=found.now.source,
        verdict=found.now.verdict,
        verdict_text=target.VERDICT_TEXT[found.now.verdict],
        best_start=best[0] if best else None,
        best_end=best[1] if best else None,
        hours=[
            TargetHourItem(
                at=h.at,
                alt=round(h.alt, 1),
                az=round(h.az, 1),
                direction=h.direction,
                clouds=h.clouds,
                moon_near=h.moon_near,
                verdict=h.verdict,
                verdict_text=target.VERDICT_TEXT[h.verdict],
            )
            for h in found.hours
        ],
    )
