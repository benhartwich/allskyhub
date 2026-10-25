"""Lightning against Meteosat's Lightning Imager (roadmap H8, SPEC §7.10).

The camera sees a flash only as sky that lights up for one frame; a lamp or a car near it
does the same. The Lightning Imager on Meteosat Third Generation (MTG-I) sees the flashes
from space. Its flash product LI-2-LFL (EUMETSAT Data Store collection ``EO:EUM:DAT:0691``)
comes as products of a few minutes each, a zip with a NetCDF-4 file ``…BODY….nc`` holding
one record per flash: ``flash_time`` (seconds since 2000-01-01 UTC), ``latitude`` and
``longitude``, possibly packed with ``scale_factor``/``add_offset``.

Every ``EVERY`` the hub reads the products of the last ``LOOKBACK`` it has not read yet and
keeps the flashes over Europe for ``KEEP``. A product it has read covers its time span,
with or without flashes; an outage leaves a gap, and what falls into a gap stays unchecked.

A lightning event is checked once ``end + MARGIN`` is covered: the nearest flash within
``REACH_KM`` in ``start - MARGIN .. end + MARGIN`` is kept with it. A storm (``data.storm``)
is over once ``STORM_GAP`` passed after its last flash; when all its flashes are checked
and none had a satellite flash within reach, each gets the automatic label
``false_positive``/``other`` (``DETAIL``). Labels set by the owner win (devices/labels.py).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import math
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import quote

import h5py  # pyright: ignore[reportMissingTypeStubs]
import httpx
import numpy as np
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import fireball
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.i18n import english, tr
from allskyhub_server.models import Device, EventRecord, LiFlash, LiGranule

log = logging.getLogger(__name__)

API = "https://api.eumetsat.int"
COLLECTION = "EO:EUM:DAT:0691"
EPOCH = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
# Europe and its edges: lat_min, lon_min, lat_max, lon_max.
EUROPE = (30.0, -30.0, 75.0, 45.0)
EVERY = dt.timedelta(minutes=10)
LOOKBACK = dt.timedelta(hours=3)
KEEP = dt.timedelta(hours=48)
REACH_KM = 400.0
# SPEC §7.10: flashes count only this close to the direction the camera saw the light in.
SECTOR_DEG = 60.0
MARGIN = dt.timedelta(seconds=60)
STORM_GAP = dt.timedelta(minutes=30)
STORM_LOOKBACK = dt.timedelta(hours=24)
# Products follow each other; this much between them still counts as covered.
SEAM = dt.timedelta(seconds=2)
DETAIL = "kein Gewitter (Blitzsatellit)"
TIMEOUT_S = 60.0
PAGE = 100
# Pointing in German: N, NO, O, …
DIRECTIONS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")
DIRECTIONS_EN = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


@dataclass(frozen=True)
class Granule:
    id: str
    start: dt.datetime
    end: dt.datetime
    entry: str  # the BODY NetCDF inside the product


class Fetcher(Protocol):
    async def search(self, start: dt.datetime, end: dt.datetime) -> list[Granule]: ...

    async def body(self, granule: Granule) -> bytes: ...


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_SPAN = re.compile(r"^(?P<start>[^/]+)/(?P<end>[^/]+)$")


def _time(text: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def granules(page: dict[str, Any]) -> list[Granule]:
    """The products of one page of the Data Store's search (OpenSearch, ``format=json``)."""
    out: list[Granule] = []
    for item in _list(page.get("features")):
        feature = _dict(item)
        props = _dict(feature.get("properties"))
        span = _SPAN.match(str(props.get("date", "")))
        titles = [
            str(_dict(e).get("title", ""))
            for e in _list(_dict(props.get("links")).get("sip-entries"))
        ]
        body = [t for t in titles if "BODY" in t and t.endswith(".nc")]
        if span is None or not body or not feature.get("id"):
            continue
        out.append(Granule(str(feature["id"]), _time(span["start"]), _time(span["end"]), body[0]))
    return out


class DataStore:
    """The EUMETSAT Data Store: a token from key and secret, search, one file of a product."""

    def __init__(self, key: str, secret: str, client: httpx.AsyncClient | None = None) -> None:
        self._auth = (key, secret)
        self.client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._token = ""
        self._token_until = dt.datetime.min.replace(tzinfo=dt.UTC)

    async def _bearer(self) -> dict[str, str]:
        now = dt.datetime.now(dt.UTC)
        if now >= self._token_until:
            r = await self.client.post(
                f"{API}/token", auth=self._auth, data={"grant_type": "client_credentials"}
            )
            r.raise_for_status()
            answer = r.json()
            self._token = str(answer["access_token"])
            expires = float(answer.get("expires_in", 3600))
            self._token_until = now + dt.timedelta(seconds=max(60.0, expires - 60))
        return {"Authorization": f"Bearer {self._token}"}

    async def search(self, start: dt.datetime, end: dt.datetime) -> list[Granule]:
        out: list[Granule] = []
        index = 0
        while True:
            r = await self._get(
                f"{API}/data/search-products/1.0.0/os",
                {
                    "format": "json", "pi": COLLECTION, "dtstart": _iso(start),
                    "dtend": _iso(end), "si": index, "c": PAGE,
                },
            )  # fmt: skip
            page = r.json()
            out += granules(page)
            index += PAGE
            if index >= min(int(page.get("totalResults", 0)), 2000):
                return out

    async def body(self, granule: Granule) -> bytes:
        url = (
            f"{API}/data/download/1.0.0/collections/{quote(COLLECTION, safe='')}"
            f"/products/{quote(granule.id, safe='')}/entry"
        )
        return (await self._get(url, {"name": granule.entry})).content

    async def _get(self, url: str, params: dict[str, str | int]) -> httpx.Response:
        """GET with the token; a 401 (the token ended early, e.g. another client asked for
        one with the same key) fetches a new token and tries once more."""
        r = await self.client.get(url, params=params, headers=await self._bearer())
        if r.status_code == 401:
            self._token_until = dt.datetime.min.replace(tzinfo=dt.UTC)
            r = await self.client.get(url, params=params, headers=await self._bearer())
        r.raise_for_status()
        return r


def _values(dataset: Any) -> np.ndarray[Any, np.dtype[np.float64]]:
    """A variable as floats, packing undone, fill values as NaN."""
    raw = np.asarray(dataset[()])
    out = raw.astype(np.float64)
    attrs = dataset.attrs
    fill = attrs.get("_FillValue")
    if fill is not None:
        out[raw == np.asarray(fill).reshape(-1)[0]] = np.nan
    scale = attrs.get("scale_factor")
    if scale is not None:
        out *= float(np.asarray(scale).reshape(-1)[0])
    offset = attrs.get("add_offset")
    if offset is not None:
        out += float(np.asarray(offset).reshape(-1)[0])
    return out


def parse(data: bytes) -> list[tuple[dt.datetime, float, float]]:
    """Flashes over Europe from an LFL BODY file (NetCDF-4), or from a zip holding one."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist() if "BODY" in n and n.endswith(".nc")]
            if not names:
                return []
            data = archive.read(names[0])
    with h5py.File(io.BytesIO(data), "r") as f:
        ds = cast(Any, f)
        if "latitude" not in ds or "flash_time" not in ds:
            return []
        lat, lon, seconds = (
            _values(ds["latitude"]),
            _values(ds["longitude"]),
            _values(ds["flash_time"]),
        )
    lat_min, lon_min, lat_max, lon_max = EUROPE
    keep = (
        np.isfinite(lat) & np.isfinite(lon) & np.isfinite(seconds)
        & (lat >= lat_min) & (lat <= lat_max) & (lon >= lon_min) & (lon <= lon_max)
    )  # fmt: skip
    return [
        (EPOCH + dt.timedelta(seconds=float(s)), float(a), float(o))
        for s, a, o in zip(seconds[keep], lat[keep], lon[keep], strict=True)
    ]


async def ingest(
    db: AsyncSession, fetcher: Fetcher, now: dt.datetime, lookback: dt.timedelta = LOOKBACK
) -> int:
    """Read the products of the last ``lookback`` not read yet; drop what is older than
    ``KEEP``. Returns the number of new flashes. Commits after every product."""
    await db.execute(delete(LiFlash).where(LiFlash.at < now - KEEP))
    await db.execute(delete(LiGranule).where(LiGranule.end < now - KEEP))
    await db.commit()
    found = await fetcher.search(now - min(lookback, KEEP), now)
    known = set(
        await db.scalars(select(LiGranule.id).where(LiGranule.id.in_([g.id for g in found])))
    )
    added = 0
    for granule in found:
        if granule.id in known:
            continue
        flashes = await asyncio.to_thread(parse, await fetcher.body(granule))
        if flashes:
            await db.execute(
                insert(LiFlash), [{"at": t, "lat": a, "lon": o} for t, a, o in flashes]
            )
        db.add(LiGranule(id=granule.id, start=granule.start, end=granule.end, flashes=len(flashes)))
        await db.commit()
        known.add(granule.id)
        added += len(flashes)
    return added


async def covered(db: AsyncSession, start: dt.datetime, end: dt.datetime) -> bool:
    """Whether the products read cover ``start .. end`` without a gap."""
    spans = (
        await db.execute(
            select(LiGranule.start, LiGranule.end)
            .where(LiGranule.end >= start - SEAM, LiGranule.start <= end + SEAM)
            .order_by(LiGranule.start)
        )
    ).all()
    reached = start
    for a, b in spans:
        if a > reached + SEAM:
            return False
        reached = max(reached, b)
        if reached >= end:
            return True
    return reached >= end


def direction(bearing: float) -> str:
    return (DIRECTIONS_EN if english() else DIRECTIONS)[round(bearing / 45) % 8]


async def nearest(
    db: AsyncSession,
    lat: float,
    lon: float,
    start: dt.datetime,
    end: dt.datetime,
    toward: float | None = None,
) -> tuple[float, float] | None:
    """(km, bearing) of the nearest flash within ``REACH_KM`` in ``start .. end``; with
    ``toward`` (a compass bearing) only flashes within ``SECTOR_DEG`` of it (SPEC §7.10)."""
    dlat = REACH_KM / 111.0
    dlon = REACH_KM / (111.0 * max(0.1, math.cos(math.radians(lat))))
    rows = (
        await db.execute(
            select(LiFlash.lat, LiFlash.lon).where(
                LiFlash.at >= start, LiFlash.at <= end,
                LiFlash.lat.between(lat - dlat, lat + dlat),
                LiFlash.lon.between(lon - dlon, lon + dlon),
            )
        )
    ).all()  # fmt: skip
    best: tuple[float, float] | None = None
    for a, o in rows:
        km = fireball.distance_km(lat, lon, a, o)
        if km > REACH_KM or (best is not None and km >= best[0]):
            continue
        bearing = fireball.bearing_deg(lat, lon, a, o)
        if toward is not None and abs((bearing - toward + 180.0) % 360.0 - 180.0) > SECTOR_DEG:
            continue
        best = (km, bearing)
    return best


def _place(status: dict[str, Any] | None) -> tuple[float, float] | None:
    settings: dict[str, Any] = (status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return float(lat), float(lon)


def _toward(event: EventRecord) -> float | None:
    """SPEC §7.10: where the camera saw the light, as a compass bearing."""
    az = (event.data or {}).get("azimuth_deg")
    return float(az) if isinstance(az, int | float) and not isinstance(az, bool) else None


def summary(event: EventRecord) -> str | None:
    """The line for the event's details; None for other kinds."""
    if event.kind != "lightning":
        return None
    if event.li_checked_at is None:
        return tr("ungeprüft", "not checked")
    if event.li_km is None or event.li_bearing_deg is None:
        toward = _toward(event)
        where = f" {tr('Richtung', 'towards')} {direction(toward)}" if toward is not None else ""
        return tr(
            f"keine Blitze im Umkreis von {REACH_KM:.0f} km{where}",
            f"no flashes within {REACH_KM:.0f} km{where}",
        )
    toward = direction(event.li_bearing_deg)
    return tr(
        f"Blitze in {event.li_km:.0f} km Richtung {toward}",
        f"flashes {event.li_km:.0f} km away towards {toward}",
    )


@dataclass(frozen=True)
class StormCheck:
    """SPEC §7.10 for one storm, for its page: why it is labelled or not."""

    unchecked: int
    clear: int  # checked, no counting flash
    confirmed: list[EventRecord]  # a counting flash, nearest first

    @property
    def text(self) -> str:
        total = self.unchecked + self.clear + len(self.confirmed)
        if self.unchecked == total:
            return "Noch nicht geprüft."
        parts: list[str] = []
        if self.confirmed:
            parts.append(f"{len(self.confirmed)} von {total} bestätigt")
        if self.clear:
            parts.append(f"{self.clear} ohne Blitz in ihrer Richtung")
        if self.unchecked:
            parts.append(f"{self.unchecked} ungeprüft (Satellitendaten fehlen)")
        return ", ".join(parts) + "."


def storm_check(flashes: list[EventRecord]) -> StormCheck:
    confirmed = sorted(
        (e for e in flashes if e.li_checked_at is not None and e.li_km is not None),
        key=lambda e: e.li_km or 0.0,
    )
    unchecked = sum(e.li_checked_at is None for e in flashes)
    return StormCheck(unchecked, len(flashes) - unchecked - len(confirmed), confirmed)


async def check(db: AsyncSession, now: dt.datetime) -> set[str]:
    """Check lightning events whose window is covered, then label storms without any
    satellite flash. Returns the cameras whose labels changed. Commits."""
    from allskyhub_server.devices import labels

    rows = (
        await db.execute(
            select(EventRecord, Device.last_status)
            .join(Device, Device.id == EventRecord.device_id)
            .where(
                EventRecord.kind == "lightning",
                # Older flashes of a storm too: they stay unchecked and keep it unlabelled.
                EventRecord.start >= now - KEEP - STORM_LOOKBACK,
            )
            .order_by(EventRecord.start)
        )
    ).all()
    storms: dict[tuple[str, str], list[EventRecord]] = defaultdict(list)
    for event, status in rows:
        data: dict[str, Any] = event.data or {}
        storm = data.get("storm")
        if isinstance(storm, str):
            storms[(event.device_id, storm)].append(event)
        if event.li_checked_at is not None or data.get("imported"):
            continue
        if event.start < now - KEEP + MARGIN:
            continue  # older than the kept flashes
        place = _place(status)
        start, end = event.start - MARGIN, event.end + MARGIN
        if place is None or end > now or not await covered(db, start, end):
            continue
        found = await nearest(db, *place, start, end, _toward(event))
        event.li_checked_at = now
        event.li_km, event.li_bearing_deg = found if found is not None else (None, None)
    changed: set[str] = set()
    for (device_id, _), flashes in storms.items():
        if max(e.end for e in flashes) + STORM_GAP > now:
            continue  # the storm may go on
        # SPEC §7.10 (Benjamin, 2026-10-15): one confirmed event keeps the storm unlabelled;
        # events in gaps of the LI data stay unchecked, without holding back the others.
        checked = [e for e in flashes if e.li_checked_at is not None]
        if not checked or any(e.li_km is not None for e in checked):
            continue
        # SPEC §7.12: the camera's own sensor decided (it labelled the storm, or it heard a
        # strike close by); the satellite then labels nothing more. Labels set by the owner
        # keep only their own event (set_auto never replaces them).
        if any(_sensor_heard(e) or _sensor_label(e) for e in flashes):
            continue
        for event in checked:
            if await labels.set_auto(db, event, "other", DETAIL, now):
                changed.add(device_id)
    await db.commit()
    return changed


SENSOR_DETAIL = "kein Blitz am Sensor"


def _sensor_label(event: EventRecord) -> bool:
    return event.label_auto and event.label_detail == SENSOR_DETAIL


def _sensor_heard(event: EventRecord) -> bool:
    km = (event.data or {}).get("sensor_km")
    return isinstance(km, int | float)


async def pending(db: AsyncSession) -> set[str]:
    """Cameras with lightning labels the hub set and the camera has not acknowledged."""
    return set(
        await db.scalars(
            select(EventRecord.device_id)
            .where(
                EventRecord.kind == "lightning",
                EventRecord.label_pending.is_(True),
                EventRecord.label_auto.is_(True),
                EventRecord.label_detail == DETAIL,
            )
            .distinct()
        )
    )


async def run_forever(
    maker: async_sessionmaker[AsyncSession], fetcher: Fetcher, registry: ConnectionRegistry
) -> None:
    from allskyhub_server.devices import labels

    while True:
        now = dt.datetime.now(dt.UTC)
        try:
            async with maker() as db:
                await ingest(db, fetcher, now)
                changed = await check(db, now)
                # Also labels set while no round could deliver them (li-backfill on the
                # command line): they would otherwise wait for the camera's next hello.
                changed |= await pending(db)
            for device_id in changed:
                if registry.is_online(device_id):
                    labels.deliver_later(maker, registry, device_id)
        except Exception:  # never let one bad round end the loop
            log.exception("lightning imager round failed")
        await asyncio.sleep(EVERY.total_seconds())
