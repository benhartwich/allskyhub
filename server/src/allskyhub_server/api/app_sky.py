"""The sky at any place for the app without login (the app's "Himmel"), and the measured
sky of a public camera as a series (also for bortle, the astrophotography cockpit).

``/sky`` and ``/calendar`` take a place the app sends rounded to 0.1° (about 10 km); the
hub rounds again and keeps nothing of it. Forecast, moon, Kp and satellite passes come
from the same caches as the cameras' "Heute Nacht" (devices/tonight.py), so a new place
costs at most one Open-Meteo call an hour per 0.1° cell; a per-IP limit keeps a client
from walking the grid.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import astro, public, satellites, showers, space_weather, tonight
from allskyhub_server.devices.space_weather import KpCache
from allskyhub_server.devices.tonight import ForecastCache, Tonight
from allskyhub_server.i18n import tr
from allskyhub_server.models import Device, SkySample
from allskyhub_server.web.deps import DbSession, SettingsDep, client_ip

router = APIRouter(prefix="/api/v1/public")

CACHE = "public, max-age=300"
NIGHTS = 3
NEARBY_KM = 60.0
# A camera's measurement counts as "now" for this long.
NEARBY_MAX_AGE = dt.timedelta(minutes=20)
CALENDAR_DAYS = 60
PASS_NIGHTS = 2
SERIES_HOURS = (6, 24, 72)
SERIES_MAX_POINTS = 1500
SKY_PER_IP = (ratelimit.Limit(60, 600), ratelimit.Limit(600, 86400))

Lat = Annotated[float, Query(ge=-90, le=90)]
Lon = Annotated[float, Query(ge=-180, le=180)]
Zone = Annotated[str, Query(max_length=64)]


class SkyHour(BaseModel):
    at: dt.datetime
    clouds: int | None
    low: int | None
    mid: int | None
    high: int | None
    rain: int | None
    wind_kmh: float | None
    moon_up: bool
    good: bool


class SkyNight(BaseModel):
    evening: dt.date
    verdict: Literal["good", "fair", "cloudy", "unknown", "bright"]
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
    good_hours: int
    hours: list[SkyHour]


class KpDay(BaseModel):
    day: dt.date
    kp: float
    predicted: bool


class Aurora(BaseModel):
    now: float | None
    days: list[KpDay]
    # Rough rule of thumb (space_weather.needed_kp): from here on low in the poleward sky.
    needed: float


class SkyPass(BaseModel):
    name: str
    start: dt.datetime
    end: dt.datetime
    max_altitude: float
    start_azimuth: float
    end_azimuth: float


class Nearby(BaseModel):
    """What the nearest public camera measures now: measured, not forecast."""

    slug: str
    title: str
    distance_km: float
    at: dt.datetime
    cloud_cover: float | None
    sqm_mag: float | None


class Sky(BaseModel):
    lat: float
    lon: float
    tz: str
    nights: list[SkyNight]
    aurora: Aurora | None
    passes: list[SkyPass]
    nearby: Nearby | None


class CalendarItem(BaseModel):
    kind: Literal["shower", "new_moon", "full_moon", "aurora", "pass", "good_night"]
    at: dt.datetime
    all_day: bool
    title: str
    text: str


class Calendar(BaseModel):
    items: list[CalendarItem]


class SeriesPoint(BaseModel):
    at: dt.datetime
    cloud_cover: float | None
    sqm_mag: float | None
    stars: int | None


class Series(BaseModel):
    slug: str
    hours: int
    # How the camera measures (SPEC §6.3 status.sky), so a client can say what it shows.
    method: dict[str, str]
    points: list[SeriesPoint]


METHOD = {
    "cloud_cover": "Allsky camera: share of the upper sky without stars at night (sun <= -12 deg), "
    "from the sky's colour by day; 0..1, approximate.",
    "sqm_mag": "Allsky camera: sky background normalised by exposure and gain, "
    "mag/arcsec², only at astronomical night; approximate until calibrated.",
    "stars": "Allsky camera: stars detected at night.",
}


async def _limit(request: Request, settings: SettingsDep) -> None:
    try:
        await ratelimit.hit(
            request.app.state.engine, f"public-sky:{client_ip(request, settings)}", SKY_PER_IP
        )
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429, "Too many requests", {"Retry-After": str(exc.retry_after)}
        ) from None


def _json(model: BaseModel) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), headers={"Cache-Control": CACHE})


def _verdict(t: Tonight) -> Literal["good", "fair", "cloudy", "unknown", "bright"]:
    if t.night.dusk is None or t.night.dawn is None:
        return "bright"  # it does not get astronomically dark
    if not any(h.clouds is not None for h in t.hours):
        return "unknown"
    good = sum(h.good for h in t.hours)
    if t.best is not None and t.best[1] - t.best[0] >= dt.timedelta(hours=2):
        return "good"
    return "fair" if good else "cloudy"


def _night(evening: dt.date, t: Tonight, forecast: dict[dt.datetime, tonight.Hour]) -> SkyNight:
    n = t.night
    hours: list[SkyHour] = []
    for h in t.hours:
        fc = forecast.get(h.at)
        hours.append(
            SkyHour(
                at=h.at,
                clouds=h.clouds,
                low=fc.low if fc else None,
                mid=fc.mid if fc else None,
                high=fc.high if fc else None,
                rain=fc.rain if fc else None,
                wind_kmh=fc.wind_kmh if fc else None,
                moon_up=h.moon_up,
                good=h.good,
            )
        )
    return SkyNight(
        evening=evening,
        verdict=_verdict(t),
        summary=t.summary,
        sunset=n.sunset,
        dusk=n.dusk,
        dawn=n.dawn,
        sunrise=n.sunrise,
        moonrise=n.moonrise,
        moonset=n.moonset,
        moon_illumination=round(n.moon_illumination, 3),
        moon_phase=t.moon_phase,
        best_start=t.best[0] if t.best else None,
        best_end=t.best[1] if t.best else None,
        good_hours=sum(h.good for h in t.hours),
        hours=hours,
    )


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


async def _nearby(db: DbSession, place: tuple[float, float], now: dt.datetime) -> Nearby | None:
    """The nearest public camera that shows its place, live image and sky values, with a
    fresh measurement."""
    rows = await db.scalars(
        select(Device).where(Device.public_slug.is_not(None), Device.owner_id.is_not(None))
    )
    best: tuple[float, Device] | None = None
    for device in rows:
        coords = public.coordinates(device)
        sections = public.profile(device).sections
        if coords is None or not (sections.live and sections.sky):
            continue
        d = distance_km(place, coords)
        if d <= NEARBY_KM and (best is None or d < best[0]):
            best = (d, device)
    if best is None:
        return None
    d, device = best
    sky: dict[str, Any] = (device.last_status or {}).get("sky") or {}
    at = device.latest_image_at
    if at is None or now - at > NEARBY_MAX_AGE:
        return None
    cloud, sqm = sky.get("cloud_cover"), sky.get("sqm_mag")
    return Nearby(
        slug=device.public_slug or "",
        title=public.title(device),
        distance_km=round(d, 0),
        at=at,
        cloud_cover=float(cloud) if isinstance(cloud, int | float) else None,
        sqm_mag=float(sqm) if isinstance(sqm, int | float) else None,
    )


async def _passes(
    request: Request,
    lat: float,
    lon: float,
    nights: list[tuple[dt.date, Tonight]],
    now: dt.datetime,
) -> list[SkyPass]:
    tles = await request.app.state.tles.get()
    if not tles:
        return []
    found: list[satellites.Pass] = []
    for _, t in nights:
        if t.night.sunset and t.night.sunrise:
            start = max(t.night.sunset, now)
            if start < t.night.sunrise:
                found += await asyncio.to_thread(
                    satellites.passes, tles, lat, lon, start, t.night.sunrise
                )
    return [
        SkyPass(
            name=p.name,
            start=p.start,
            end=p.end,
            max_altitude=round(p.max_altitude),
            start_azimuth=round(p.start_azimuth),
            end_azimuth=round(p.end_azimuth),
        )
        for p in found
    ]


async def _aurora(request: Request, lat: float) -> Aurora | None:
    cache: KpCache = request.app.state.kp
    kp = await cache.get()
    if kp is None:
        return None
    return Aurora(
        now=kp.now,
        days=[KpDay(day=d.day, kp=d.kp, predicted=d.predicted) for d in kp.days],
        needed=round(space_weather.needed_kp(lat), 1),
    )


async def _nights(
    request: Request, lat: float, lon: float, tz: str, now: dt.datetime
) -> tuple[list[tuple[dt.date, Tonight]], dict[dt.datetime, tonight.Hour]]:
    cache: ForecastCache = request.app.state.forecast
    forecast = await cache.get(lat, lon)
    zone = tonight.zone_for(tz)
    nights = await asyncio.to_thread(tonight.coming_nights, now, lat, lon, zone, forecast, NIGHTS)
    return nights, {h.at: h for h in forecast}


@router.get("/sky")
async def sky(
    request: Request, db: DbSession, settings: SettingsDep, lat: Lat, lon: Lon, tz: Zone = ""
) -> JSONResponse:
    """The next three nights, aurora, satellite passes and the nearest camera's sky."""
    await _limit(request, settings)
    lat, lon = round(lat, 1), round(lon, 1)
    now = dt.datetime.now(dt.UTC)
    nights, forecast = await _nights(request, lat, lon, tz, now)
    return _json(
        Sky(
            lat=lat,
            lon=lon,
            tz=tonight.zone_for(tz).key,
            nights=[_night(evening, t, forecast) for evening, t in nights],
            aurora=await _aurora(request, lat),
            passes=await _passes(request, lat, lon, nights[:1], now),
            nearby=await _nearby(db, (lat, lon), now),
        )
    )


MoonKind = Literal["new_moon", "full_moon"]


def moon_phases(start: dt.datetime, days: int) -> list[tuple[MoonKind, dt.datetime]]:
    """New and full moons in [start, start + days), to within about an hour."""
    step = dt.timedelta(hours=6)
    t = start
    values: list[tuple[dt.datetime, float]] = []
    while t < start + dt.timedelta(days=days) + 2 * step:
        values.append((t, astro.moon_illumination(t)))
        t += step
    found: list[tuple[MoonKind, dt.datetime]] = []
    for (_, a), (tb, b), (_, c) in zip(values, values[1:], values[2:], strict=False):
        kind: MoonKind | None = (
            "new_moon" if b <= a and b < c else "full_moon" if b >= a and b > c else None
        )
        if kind is None:
            continue
        # Refine to the hour around the 6-hour extreme.
        hours = [tb + dt.timedelta(hours=h) for h in range(-6, 7)]
        pick = min if kind == "new_moon" else max
        at = pick(hours, key=astro.moon_illumination)
        if start <= at < start + dt.timedelta(days=days):
            found.append((kind, at))
    return found


def _direction(az: float) -> str:
    names = (
        tr("Nord", "north"), tr("Nordost", "north-east"), tr("Ost", "east"),
        tr("Südost", "south-east"), tr("Süd", "south"), tr("Südwest", "south-west"),
        tr("West", "west"), tr("Nordwest", "north-west"),
    )  # fmt: skip
    return names[round(az / 45) % 8]


@router.get("/calendar")
async def calendar(
    request: Request, settings: SettingsDep, lat: Lat, lon: Lon, tz: Zone = ""
) -> JSONResponse:
    """What comes in the sky: shower maxima, new and full moon, aurora chances, bright
    satellite passes of the next nights and the clear nights of the forecast."""
    await _limit(request, settings)
    lat, lon = round(lat, 1), round(lon, 1)
    now = dt.datetime.now(dt.UTC)
    zone = tonight.zone_for(tz)
    today = now.astimezone(zone).date()
    items: list[CalendarItem] = []

    for s in showers.SHOWERS:
        days = showers.days_to_peak(s, today)
        if days < 0:
            days += 365
        if days <= CALENDAR_DAYS:
            peak = today + dt.timedelta(days=days)
            items.append(
                CalendarItem(
                    kind="shower",
                    at=dt.datetime.combine(peak, dt.time(22), zone).astimezone(dt.UTC),
                    all_day=True,
                    title=tr(f"{s.name}-Maximum", f"{s.name} peak"),
                    text=tr(
                        f"Unter dunklem Himmel bis etwa {s.zhr} Meteore pro Stunde, am meisten "
                        "nach Mitternacht.",
                        f"Under a dark sky up to about {s.zhr} meteors an hour, most after "
                        "midnight.",
                    ),
                )
            )

    for kind, at in moon_phases(now, CALENDAR_DAYS):
        full = kind == "full_moon"
        items.append(
            CalendarItem(
                kind=kind,
                at=at,
                all_day=True,
                title=tr("Vollmond", "Full moon") if full else tr("Neumond", "New moon"),
                text=tr(
                    "Helle Nächte, schwache Sterne und Meteore gehen unter.",
                    "Bright nights; faint stars and meteors are lost.",
                )
                if full
                else tr("Die dunkelsten Nächte des Monats.", "The darkest nights of the month."),
            )
        )

    aurora = await _aurora(request, lat)
    if aurora is not None:
        for d in aurora.days:
            if d.kp >= aurora.needed and d.day >= today:
                items.append(
                    CalendarItem(
                        kind="aurora",
                        at=dt.datetime.combine(d.day, dt.time(21), zone).astimezone(dt.UTC),
                        all_day=True,
                        title=tr("Polarlicht möglich", "Aurora possible"),
                        text=tr(
                            f"Kp-Vorhersage {d.kp:.0f}, bei dir ab etwa Kp {aurora.needed:.0f}.",
                            f"Kp forecast {d.kp:.0f}; where you are from about Kp "
                            f"{aurora.needed:.0f}.",
                        ),
                    )
                )

    nights, _ = await _nights(request, lat, lon, tz, now)
    for _, t in nights:
        if _verdict(t) == "good" and t.best:
            start, end = t.fmt(t.best[0]), t.fmt(t.best[1])
            items.append(
                CalendarItem(
                    kind="good_night",
                    at=t.best[0],
                    all_day=False,
                    title=tr("Klare, dunkle Nacht", "Clear, dark night"),
                    text=tr(
                        f"Laut Vorhersage {start} bis {end} Uhr.", f"Forecast: {start} to {end}."
                    ),
                )
            )
    for p in await _passes(request, lat, lon, nights[:PASS_NIGHTS], now):
        minutes = max(1, round((p.end - p.start).total_seconds() / 60))
        items.append(
            CalendarItem(
                kind="pass",
                at=p.start,
                all_day=False,
                title=p.name,
                text=tr(
                    f"{_direction(p.start_azimuth)} → {_direction(p.end_azimuth)}, bis "
                    f"{p.max_altitude:.0f}° hoch, {minutes} Min.",
                    f"{_direction(p.start_azimuth)} → {_direction(p.end_azimuth)}, up to "
                    f"{p.max_altitude:.0f}° high, {minutes} min.",
                ),
            )
        )
    items.sort(key=lambda i: i.at)
    return _json(Calendar(items=items))


@router.get("/cameras/{slug}/sky-series")
async def sky_series(db: DbSession, slug: str, hours: int = 24) -> JSONResponse:
    """The camera's measured sky over the last ``hours`` (6, 24 or 72), UTC, only when its
    public page shows the sky values."""
    if hours not in SERIES_HOURS:
        raise HTTPException(422, f"hours must be one of {SERIES_HOURS}")
    device = await public.by_slug(db, slug)
    if device is None or not public.profile(device).sections.sky:
        raise HTTPException(404)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    rows = list(
        await db.scalars(
            select(SkySample)
            .where(SkySample.device_id == device.id, SkySample.at >= since)
            .order_by(SkySample.at)
        )
    )
    stride = max(1, math.ceil(len(rows) / SERIES_MAX_POINTS))
    points = [
        SeriesPoint(at=r.at, cloud_cover=r.cloud_cover, sqm_mag=r.sqm_mag, stars=r.stars)
        for r in rows[::stride]
    ]
    model = Series(slug=slug, hours=hours, method=METHOD, points=points)
    return JSONResponse(
        model.model_dump(mode="json"), headers={"Cache-Control": "public, max-age=60"}
    )
