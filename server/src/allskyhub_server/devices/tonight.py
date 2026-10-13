"""'Heute Nacht': when the coming night is dark and clear at a camera (roadmap C1).

Cloud forecast from Open-Meteo (free, no key, CC BY 4.0), fetched per 0.1° cell at most once
an hour; darkness and moon from ``astro``. A dark hour is good when the forecast cloud cover
is below ``CLEAR_BELOW`` and the moon is down or less than ``BRIGHT_MOON`` lit.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from allskyhub_server.devices import astro, satellites
from allskyhub_server.devices.refresh import Refreshing

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_AGE_S = 3600
TIMEOUT_S = 4.0
CLEAR_BELOW = 30  # % cloud cover
BRIGHT_MOON = 0.3  # illuminated fraction
DEFAULT_ZONE = "Europe/Berlin"


@dataclass(frozen=True)
class Hour:
    at: dt.datetime  # UTC, start of the hour
    clouds: int  # %
    low: int
    mid: int
    high: int
    rain: int | None  # % probability


def parse_forecast(payload: Any) -> list[Hour]:
    """Open-Meteo hourly answer with ``timeformat=unixtime``; tolerant of missing series."""
    hourly = cast(dict[str, Any], payload).get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict):
        return []
    h = cast(dict[str, list[Any]], hourly)

    def at(key: str, i: int) -> int | None:
        values = h.get(key) or []
        v = values[i] if i < len(values) else None
        return round(v) if isinstance(v, int | float) else None

    hours: list[Hour] = []
    for i, ts in enumerate(h.get("time") or []):
        clouds = at("cloud_cover", i)
        if not isinstance(ts, int | float) or clouds is None:
            continue
        hours.append(
            Hour(
                at=dt.datetime.fromtimestamp(ts, dt.UTC),
                clouds=clouds,
                low=at("cloud_cover_low", i) or 0,
                mid=at("cloud_cover_mid", i) or 0,
                high=at("cloud_cover_high", i) or 0,
                rain=at("precipitation_probability", i),
            )
        )
    return hours


class ForecastCache:
    """Per 0.1° cell, at most once an hour; a page never waits for Open-Meteo (``refresh``)."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._cells: Refreshing[tuple[float, float], list[Hour]] = Refreshing(
            self._fetch, MAX_AGE_S
        )

    async def get(self, lat: float, lon: float) -> list[Hour]:
        return await self._cells.get((round(lat, 1), round(lon, 1))) or []

    async def _fetch(self, key: tuple[float, float]) -> list[Hour] | None:
        params: dict[str, str | float] = {
            "latitude": key[0],
            "longitude": key[1],
            "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
            "precipitation_probability",
            "timezone": "UTC",
            "timeformat": "unixtime",
            "forecast_days": 3,
        }
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
        try:
            r = await client.get(FORECAST_URL, params=params, timeout=TIMEOUT_S)
            r.raise_for_status()
            return parse_forecast(r.json())
        except (httpx.HTTPError, ValueError):
            return None
        finally:
            if self._client is None:
                await client.aclose()


@dataclass(frozen=True)
class NightHour:
    at: dt.datetime  # UTC
    clouds: int | None
    moon_up: bool
    good: bool


@dataclass(frozen=True)
class Tonight:
    zone: ZoneInfo
    night: astro.Night
    hours: list[NightHour]  # the dark hours (dusk to dawn)
    best: tuple[dt.datetime, dt.datetime] | None  # longest good stretch
    summary: str
    passes: list[satellites.Pass] = field(default_factory=list[satellites.Pass])

    def fmt(self, t: dt.datetime | None) -> str:
        """Local "HH:MM" for templates; "–" for none."""
        return "–" if t is None else _clock(t, self.zone)

    @property
    def moon_events(self) -> list[tuple[str, dt.datetime]]:
        """Moonrise and moonset of this night in the order they happen."""
        events = [("auf", self.night.moonrise), ("unter", self.night.moonset)]
        return sorted(((name, t) for name, t in events if t is not None), key=lambda e: e[1])

    @property
    def moon_phase(self) -> str:
        return moon_phase_name(self.night.moon_illumination, self.night.moon_waxing)


async def for_device(
    cache: ForecastCache,
    status: dict[str, Any] | None,
    now: dt.datetime,
    tles: satellites.TleCache | None = None,
) -> Tonight | None:
    """None while the camera has not reported its location."""
    settings = cast(dict[str, Any], (status or {}).get("settings") or {})
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    zone = zone_for(settings.get("timezone"))
    result = build(now, float(lat), float(lon), zone, await cache.get(float(lat), float(lon)))
    night = result.night
    if tles is not None and night.sunset and night.sunrise:
        # Roadmap C6: passes still ahead tonight (computed off the event loop).
        start = max(night.sunset, now)
        found = await asyncio.to_thread(
            satellites.passes, await tles.get(), float(lat), float(lon), start, night.sunrise
        )
        result = dataclasses.replace(result, passes=found)
    return result


def zone_for(name: object) -> ZoneInfo:
    try:
        return ZoneInfo(name) if isinstance(name, str) and name else ZoneInfo(DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZONE)


def _evening_noon(now: dt.datetime, zone: ZoneInfo, lat: float, lon: float) -> dt.datetime:
    """Local noon of the evening whose night is still ahead or running."""
    local = now.astimezone(zone)
    today = dt.datetime.combine(local.date(), dt.time(12), zone)
    yesterday = today - dt.timedelta(days=1)
    night = astro.night_after(yesterday.astimezone(dt.UTC), lat, lon)
    end = night.dawn or night.sunrise
    if end is not None and now < end:
        return yesterday.astimezone(dt.UTC)
    return today.astimezone(dt.UTC)


def _clock(t: dt.datetime, zone: ZoneInfo) -> str:
    return t.astimezone(zone).strftime("%H:%M")


def build(
    now: dt.datetime, lat: float, lon: float, zone: ZoneInfo, forecast: list[Hour]
) -> Tonight:
    noon = _evening_noon(now, zone, lat, lon)
    night = astro.night_after(noon, lat, lon)
    by_hour = {h.at: h for h in forecast}
    hours: list[NightHour] = []
    if night.dusk and night.dawn:
        t = night.dusk.replace(minute=0, second=0)
        while t < night.dawn:
            mid = t + dt.timedelta(minutes=30)
            fc = by_hour.get(t)
            moon_up = astro.moon_altitude(mid, lat, lon) > 0
            bright = moon_up and night.moon_illumination >= BRIGHT_MOON
            good = fc is not None and fc.clouds < CLEAR_BELOW and not bright
            hours.append(NightHour(t, fc.clouds if fc else None, moon_up, good))
            t += dt.timedelta(hours=1)
    best: tuple[dt.datetime, dt.datetime] | None = None
    run_start: dt.datetime | None = None
    for i, h in enumerate([*hours, NightHour(now, None, False, False)]):
        if h.good and run_start is None:
            run_start = h.at
        elif not h.good and run_start is not None:
            end = hours[i - 1].at + dt.timedelta(hours=1)
            if best is None or end - run_start > best[1] - best[0]:
                best = (run_start, end)
            run_start = None
    if best and night.dusk and night.dawn:
        best = (max(best[0], night.dusk), min(best[1], night.dawn))
    return Tonight(zone, night, hours, best, _summary(night, hours, best, zone))


def _summary(
    night: astro.Night,
    hours: list[NightHour],
    best: tuple[dt.datetime, dt.datetime] | None,
    zone: ZoneInfo,
) -> str:
    if night.dusk is None or night.dawn is None:
        return "Heute wird es nicht astronomisch dunkel."
    if not any(h.clouds is not None for h in hours):
        return "Keine Wolkenvorhersage verfügbar."
    if best is None:
        if all(h.clouds is not None and h.clouds >= CLEAR_BELOW for h in hours):
            return "Bewölkt, die ganze Nacht."
        return "Klare Abschnitte nur bei hellem Mond."
    text = f"Klar und dunkel von {_clock(best[0], zone)} bis {_clock(best[1], zone)} Uhr."
    if night.moon_illumination >= BRIGHT_MOON and night.moonset and best[0] >= night.moonset:
        text += f" Der Mond geht um {_clock(night.moonset, zone)} unter."
    return text


def moon_phase_name(illumination: float, waxing: bool) -> str:
    if illumination < 0.03:
        return "Neumond"
    if illumination > 0.97:
        return "Vollmond"
    if 0.4 <= illumination <= 0.6:
        return "zunehmender Halbmond" if waxing else "abnehmender Halbmond"
    if illumination < 0.4:
        return "zunehmende Sichel" if waxing else "abnehmende Sichel"
    return "zunehmender Mond" if waxing else "abnehmender Mond"
