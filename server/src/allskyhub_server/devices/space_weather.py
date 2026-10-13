"""Kp forecast for the aurora box on public pages (NOAA SWPC, public domain data).

Fetched at most once an hour and kept in memory; the page never waits long for it and
shows nothing when NOAA is unreachable."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, cast

import httpx

from allskyhub_server.devices.refresh import Refreshing

KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
MAX_AGE_S = 3600
TIMEOUT_S = 4.0


@dataclass(frozen=True)
class KpDay:
    day: dt.date
    kp: float  # highest 3-hour Kp of the (UTC) day
    predicted: bool


@dataclass(frozen=True)
class KpForecast:
    now: float | None  # the latest observed or estimated Kp
    days: list[KpDay]


def parse(payload: Any, today: dt.date) -> KpForecast:
    """NOAA's list of {time_tag, kp, observed}; older answers were rows with a header row."""
    rows: list[tuple[dt.datetime, float, bool]] = []
    for item in cast(list[Any], payload) if isinstance(payload, list) else []:
        if isinstance(item, dict):
            row = cast(dict[str, Any], item)
            tag, kp, kind = row.get("time_tag"), row.get("kp"), row.get("observed")
        elif isinstance(item, list) and len(cast(list[Any], item)) >= 3:
            tag, kp, kind = cast(list[Any], item)[:3]
        else:
            continue
        if not isinstance(kp, int | float | str):
            continue
        try:
            when = dt.datetime.fromisoformat(str(tag).replace(" ", "T"))
            value = float(kp)
        except (TypeError, ValueError):
            continue
        rows.append((when, value, kind == "predicted"))
    current = [kp for _, kp, predicted in rows if not predicted]
    by_day: dict[dt.date, KpDay] = {}
    for when, kp, predicted in rows:
        day = when.date()
        if day < today:
            continue
        old = by_day.get(day)
        if old is None or kp > old.kp:
            by_day[day] = KpDay(day, kp, predicted or (old is not None and old.predicted))
    days = [by_day[d] for d in sorted(by_day)][:3]
    return KpForecast(current[-1] if current else None, days)


def needed_kp(latitude: float) -> float:
    """Rough rule of thumb: the Kp from which aurora can show low on the poleward horizon."""
    return max(0.0, min(9.0, (65 - abs(latitude)) / 2.2))


class KpCache:
    """At most once an hour; a page never waits for NOAA (``refresh``)."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._kp: Refreshing[str, KpForecast] = Refreshing(self._fetch, MAX_AGE_S)

    async def get(self) -> KpForecast | None:
        return await self._kp.get("kp")

    async def _fetch(self, _: str) -> KpForecast | None:
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
        try:
            r = await client.get(KP_URL, timeout=TIMEOUT_S)
            r.raise_for_status()
            return parse(r.json(), dt.datetime.now(dt.UTC).date())
        except (httpx.HTTPError, ValueError):
            return None
        finally:
            if self._client is None:
                await client.aclose()


# Roadmap C13: NOAA's OVATION aurora forecast map (public domain), kept for 10 minutes.
OVATION_URL = "https://services.swpc.noaa.gov/images/animations/ovation/{hemisphere}/latest.jpg"
OVATION_MAX_AGE_S = 600
OVATION_MAX_BYTES = 2_000_000


class OvationCache:
    """The hub serves the map itself: visitors' browsers never talk to NOAA (CSP, privacy)."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._maps: Refreshing[str, bytes] = Refreshing(self._fetch, OVATION_MAX_AGE_S)

    async def get(self, hemisphere: str) -> bytes | None:
        if hemisphere not in ("north", "south"):
            return None
        return await self._maps.get(hemisphere)

    async def _fetch(self, hemisphere: str) -> bytes | None:
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
        try:
            r = await client.get(OVATION_URL.format(hemisphere=hemisphere))
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        finally:
            if self._client is None:
                await client.aclose()
        body = r.content
        return body if body.startswith(b"\xff\xd8") and len(body) <= OVATION_MAX_BYTES else None
