"""Measurement series for the chart viewer (charts.js): sky values, the camera's own state
and the sun's and moon's altitude, as JSON columns over one time range.

Times are Unix seconds. A pause longer than ``GAP_S`` (camera off, daytime without SQM)
gets an explicit ``null`` so the line breaks there instead of bridging it. Long ranges are
averaged into ``MAX_POINTS`` buckets; the answer says so (``bucket_s``), so nothing looks
more precise than it is.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.devices import astro, tonight
from allskyhub_server.models import Device, NightStat, SkySample, StatusSample

GAP_S = 15 * 60
MAX_POINTS = 2000
MAX_RANGE = dt.timedelta(days=31)
HOURS = (24, 72, 168, 720)

Column = list[float | None]


@dataclass(frozen=True)
class Field:
    key: str
    value: Callable[[Any], float | None]


SKY_FIELDS = (
    Field("cloud", lambda s: None if s.cloud_cover is None else round(s.cloud_cover * 100, 1)),
    Field("sqm", lambda s: s.sqm_mag),
    Field("stars", lambda s: None if s.stars is None else float(s.stars)),
)
STATUS_FIELDS = (
    Field("exposure", lambda s: None if not s.exposure_us else s.exposure_us / 1e6),
    Field("gain", lambda s: s.gain),
    Field("sensor", lambda s: s.sensor_temp_c),
    Field("cpu", lambda s: s.cpu_temp_c),
    Field("disk", lambda s: s.disk_free_pct),
)


def zone(device: Device) -> ZoneInfo:
    """The camera's own time zone (status settings), else the hub's."""
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    return tonight.zone_for(settings.get("timezone"))


def night_range(night_id: str, tz: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
    """Local noon to the next local noon (the night rule of SPEC §4.5)."""
    day = dt.date(int(night_id[:4]), int(night_id[4:6]), int(night_id[6:8]))
    start = dt.datetime.combine(day, dt.time(12), tz)
    return start, start + dt.timedelta(days=1)


@dataclass(frozen=True)
class Range:
    start: dt.datetime
    end: dt.datetime
    night_id: str | None  # set when the range is one night
    hours: int | None  # set when the range is the last hours


async def resolve(
    db: AsyncSession, device: Device, night: str | None, hours: int | None, now: dt.datetime
) -> Range:
    """The asked range: one night (``night`` YYYYMMDD), the last ``hours`` (one of
    ``HOURS``), or by default the newest night with sky data (else the last 24 hours)."""
    if hours in HOURS:
        return Range(now - dt.timedelta(hours=hours), now, None, hours)
    if night is None or not (len(night) == 8 and night.isdigit()):
        night = await latest_night(db, device.id)
    if night is not None:
        try:
            start, end = night_range(night, zone(device))
        except ValueError:
            pass
        else:
            return Range(start, end, night, None)
    return Range(now - dt.timedelta(hours=24), now, None, 24)


def _ts(at: dt.datetime) -> float:
    return round(at.timestamp(), 1)


def columns(rows: Sequence[Any], fields: Sequence[Field], bucket_s: int) -> dict[str, Column]:
    """``t`` plus one column per field; means per bucket when ``bucket_s`` > 0, ``None``
    across pauses longer than ``GAP_S``. ``rows`` have an ``at`` and are sorted by it."""
    t: Column = []
    out: dict[str, Column] = {f.key: [] for f in fields}
    gap = max(GAP_S, 2 * bucket_s)

    def emit(at: float, values: list[float | None]) -> None:
        if t and at - (t[-1] or 0) > gap:
            t.append((t[-1] or 0) + 1)
            for f in fields:
                out[f.key].append(None)
        t.append(at)
        for f, v in zip(fields, values, strict=True):
            out[f.key].append(v)

    if bucket_s <= 0:
        for row in rows:
            emit(_ts(row.at), [f.value(row) for f in fields])
    else:
        current: int | None = None
        sums: list[list[float]] = []
        times: list[float] = []

        def flush() -> None:
            if current is None:
                return
            means = [round(sum(s) / len(s), 4) if s else None for s in sums]
            emit(round(sum(times) / len(times), 1), means)

        for row in rows:
            at = row.at.timestamp()
            key = int(at // bucket_s)
            if key != current:
                flush()
                current, sums, times = key, [[] for _ in fields], []
            times.append(at)
            for i, f in enumerate(fields):
                v = f.value(row)
                if v is not None and math.isfinite(v):
                    sums[i].append(v)
        flush()
    return {"t": t, **out}


def ephemeris(start: dt.datetime, end: dt.datetime, lat: float, lon: float) -> dict[str, Column]:
    """Sun and moon altitude (degrees) every few minutes, for twilight bands and the moon."""
    span = (end - start).total_seconds()
    step = 300 if span <= 3 * 86400 else (900 if span <= 8 * 86400 else 1800)
    t: Column = []
    sun: Column = []
    moon: Column = []
    at = start
    while at <= end:
        t.append(_ts(at))
        sun.append(round(astro.sun_altitude(at, lat, lon), 2))
        moon.append(round(astro.moon_altitude(at, lat, lon), 2))
        at += dt.timedelta(seconds=step)
    middle = start + (end - start) / 2
    return {
        "t": t,
        "sun": sun,
        "moon": moon,
        "moon_illumination": [round(astro.moon_illumination(middle), 3)],
    }


def location(device: Device, public: bool) -> tuple[float, float] | None:
    """The camera's place; for public pages rounded to 0.5° (twilight still right to a few
    minutes, the place no more exact than the page's own coordinates)."""
    settings: dict[str, Any] = (device.last_status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    if public:
        return round(float(lat) * 2) / 2, round(float(lon) * 2) / 2
    return float(lat), float(lon)


async def latest_night(db: AsyncSession, device_id: str) -> str | None:
    return await db.scalar(
        select(func.max(SkySample.night_id)).where(SkySample.device_id == device_id)
    )


async def load(
    db: AsyncSession,
    device: Device,
    rng: Range,
    *,
    with_status: bool,
    public: bool,
) -> dict[str, Any]:
    """Everything the viewer draws for the range (at most ``MAX_RANGE``)."""
    end = rng.end
    start = max(rng.start, end - MAX_RANGE)
    span = (end - start).total_seconds()
    # One-minute frames give ~1440 points a day; beyond MAX_POINTS average into buckets.
    bucket_s = 0 if span <= 2 * 86400 else int(math.ceil(span / MAX_POINTS / 60) * 60)
    sky_rows = await db.scalars(
        select(SkySample)
        .where(SkySample.device_id == device.id, SkySample.at >= start, SkySample.at < end)
        .order_by(SkySample.at)
    )
    result: dict[str, Any] = {
        "from": _ts(start),
        "to": _ts(end),
        "tz": zone(device).key,
        "night": rng.night_id,
        "hours": rng.hours,
        "bucket_s": bucket_s,
        "sky": columns(list(sky_rows), SKY_FIELDS, bucket_s),
    }
    if with_status:
        status_rows = await db.scalars(
            select(StatusSample)
            .where(
                StatusSample.device_id == device.id,
                StatusSample.at >= start,
                StatusSample.at < end,
            )
            .order_by(StatusSample.at)
        )
        result["status"] = columns(list(status_rows), STATUS_FIELDS, bucket_s)
    place = location(device, public)
    if place is not None:
        result["ephemeris"] = ephemeris(start, end, *place)
    return result


async def nights(db: AsyncSession, device: Device, year: int) -> dict[str, Any]:
    """One row per night of ``year`` (NightStat): darkest sky, clear hours, meteors, and the
    moon's illumination at local midnight, for the long-term chart."""
    tz = zone(device)
    rows = await db.scalars(
        select(NightStat)
        .where(NightStat.device_id == device.id, NightStat.night_id.like(f"{year}%"))
        .order_by(NightStat.night_id)
    )
    out: dict[str, Column] = {"t": [], "best_sqm": [], "clear_hours": [], "meteors": [], "moon": []}
    for row in rows:
        start, _ = night_range(row.night_id, tz)
        midnight = start + dt.timedelta(hours=12)
        out["t"].append(_ts(midnight))
        out["best_sqm"].append(row.best_sqm)
        out["clear_hours"].append(row.clear_hours)
        out["meteors"].append(float(row.meteors))
        out["moon"].append(round(astro.moon_illumination(midnight) * 100, 1))
    return {"tz": tz.key, "year": year, "nights": out}
