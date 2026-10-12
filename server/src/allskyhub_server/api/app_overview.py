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
from allskyhub_server.devices import health, night_summary, queries, series, tonight
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.models import Device, User
from allskyhub_server.web.deps import DbSession

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
