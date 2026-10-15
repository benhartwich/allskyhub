"""Lightning against Meteosat's Lightning Imager (roadmap H8, SPEC §7.10)."""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from typing import Any, cast

import h5py  # pyright: ignore[reportMissingTypeStubs]
import httpx
import numpy as np
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import labels, lightning_li
from allskyhub_server.devices.event_text import event_rows
from allskyhub_server.models import EventRecord, LiFlash
from tests.test_aircraft_labels import LAT, LON, _camera  # pyright: ignore[reportPrivateUsage]

NOW = dt.datetime(2026, 10, 15, 3, 0, tzinfo=dt.UTC)
NIGHT = "20261014"
# Reading two hours after the storms, checking later still.
READ = dt.datetime(2026, 10, 15, 0, 0, tzinfo=dt.UTC)


ROW = "Blitzsatellit (Quelle: EUMETSAT)"


def body(flashes: list[tuple[dt.datetime, float, float]], packed: bool = True) -> bytes:
    """A small LFL BODY file: lat/lon packed like the real product, one fill value."""
    out = io.BytesIO()
    with h5py.File(out, "w") as h5:
        f = cast(Any, h5)
        seconds = [(t - lightning_li.EPOCH).total_seconds() for t, _, _ in flashes]
        lats = [a for _, a, _ in flashes] + [0.0]
        lons = [o for _, _, o in flashes] + [0.0]
        if packed:
            lat = f.create_dataset(
                "latitude", data=np.array([round(a / 0.0027) for a in lats[:-1]] + [-32767],
                                          dtype=np.int16))  # fmt: skip
            lat.attrs["scale_factor"] = np.float32(0.0027)
            lat.attrs["_FillValue"] = np.int16(-32767)
            lon = f.create_dataset(
                "longitude", data=np.array([round(o / 0.0027) for o in lons[:-1]] + [-32767],
                                           dtype=np.int16))  # fmt: skip
            lon.attrs["scale_factor"] = np.float32(0.0027)
            lon.attrs["_FillValue"] = np.int16(-32767)
        else:
            f.create_dataset("latitude", data=np.array(lats, dtype=np.float32))
            f.create_dataset("longitude", data=np.array(lons, dtype=np.float32))
        f.create_dataset("flash_time", data=np.array([*seconds, 1e3], dtype=np.float64))
    return out.getvalue()


class FakeStore:
    def __init__(self) -> None:
        self.products: dict[str, tuple[lightning_li.Granule, bytes]] = {}
        self.downloads = 0

    def add(
        self, start: dt.datetime, flashes: list[tuple[dt.datetime, float, float]],
        minutes: int = 10,
    ) -> None:  # fmt: skip
        gid = f"W_XX-EUMETSAT-Darmstadt,LI-2-LFL-{start:%Y%m%d%H%M%S}"
        granule = lightning_li.Granule(
            gid, start, start + dt.timedelta(minutes=minutes), f"{gid}_BODY_0001.nc"
        )
        self.products[gid] = (granule, body(flashes))

    async def search(self, start: dt.datetime, end: dt.datetime) -> list[lightning_li.Granule]:
        return [g for g, _ in self.products.values() if g.end >= start and g.start <= end]

    async def body(self, granule: lightning_li.Granule) -> bytes:
        self.downloads += 1
        return self.products[granule.id][1]


def test_parse_unpacks_and_keeps_europe() -> None:
    t = dt.datetime(2026, 10, 14, 22, 0, 5, tzinfo=dt.UTC)
    data = body([(t, 48.5, 14.0), (t, -20.0, 14.0), (t, 48.0, 60.0)])
    [(at, lat, lon)] = lightning_li.parse(data)
    assert abs((at - t).total_seconds()) < 1e-3
    assert abs(lat - 48.5) < 0.003
    assert abs(lon - 14.0) < 0.003
    # Unpacked floats, and the BODY inside a zip as the Data Store serves whole products.
    plain = body([(t, 47.0, 13.0)], packed=False)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("x_TRAIL_0001.nc", b"")
        z.writestr("x_BODY_0001.nc", plain)
    assert [(a, o) for _, a, o in lightning_li.parse(archive.getvalue())] == [(47.0, 13.0)]


def test_search_page_and_summary() -> None:
    page = {
        "totalResults": 1,
        "features": [{
            "id": "LI-2-LFL-1",
            "properties": {
                "date": "2026-10-14T22:00:00Z/2026-10-14T22:10:00Z",
                "links": {"sip-entries": [{"title": "a_TRAIL_0001.nc"},
                                          {"title": "a_BODY_0001.nc"}]},
            },
        }],
    }  # fmt: skip
    [g] = lightning_li.granules(page)
    assert (g.id, g.entry) == ("LI-2-LFL-1", "a_BODY_0001.nc")
    assert g.end - g.start == dt.timedelta(minutes=10)
    assert lightning_li.direction(315) == "NW"
    event = EventRecord(kind="lightning", li_checked_at=None, data={})
    assert lightning_li.summary(event) == "ungeprüft"
    event.li_checked_at = NOW
    assert lightning_li.summary(event) == "keine Blitze im Umkreis von 400 km"
    event.li_km, event.li_bearing_deg = 231.4, 320.0
    assert lightning_li.summary(event) == "Blitze in 231 km Richtung NW"
    assert lightning_li.summary(EventRecord(kind="meteor", data={})) is None


def _flash(device_id: str, start: dt.datetime, storm: str, **data: object) -> EventRecord:
    return EventRecord(
        device_id=device_id, event_id=f"lightning-{start:%Y%m%dT%H%M%S}Z", night_id=NIGHT,
        kind="lightning", start=start, end=start + dt.timedelta(seconds=60), confidence=0.9,
        has_image=True, data={"area_frac": 0.4, "peak": 0.3, "storm": storm, **data},
    )  # fmt: skip


async def test_storm_without_satellite_flashes_is_labelled(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev, _, _ = await _camera(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    t0 = dt.datetime(2026, 10, 14, 22, 0, tzinfo=dt.UTC)
    store = FakeStore()
    for i in range(6):  # 22:00 .. 23:00 covered
        store.add(t0 + dt.timedelta(minutes=10 * i), [])
    # A real storm 230 km north-west, during the second storm only.
    far = (LAT + 1.45, LON - 2.0)
    store.add(t0 + dt.timedelta(minutes=60), [(t0 + dt.timedelta(minutes=62), *far)])
    async with maker() as db:
        db.add_all([
            _flash(dev.device_id, t0 + dt.timedelta(minutes=5), "storm-A"),
            _flash(dev.device_id, t0 + dt.timedelta(minutes=15), "storm-A"),
            _flash(dev.device_id, t0 + dt.timedelta(minutes=61), "storm-B"),
            _flash(dev.device_id, t0 + dt.timedelta(minutes=5, seconds=30), "storm-C",
                   imported=True),
        ])  # fmt: skip
        await db.commit()

    async with maker() as db:
        assert await lightning_li.ingest(db, store, READ) == 1
        assert await db.scalar(select(func.count()).select_from(LiFlash)) == 1
        assert await lightning_li.ingest(db, store, READ) == 0  # nothing read twice
        assert store.downloads == 7
        assert await lightning_li.check(db, NOW) == {dev.device_id}
    async with maker() as db:
        rows = {
            e.data["storm"] + e.start.strftime("%M"): e
            for e in await db.scalars(select(EventRecord))
        }
    for key in ("storm-A05", "storm-A15"):
        e = rows[key]
        assert (e.label, e.label_reason, e.label_auto) == ("false_positive", "other", True)
        assert e.label_detail == lightning_li.DETAIL
        assert e.label_pending
        assert dict(event_rows(e))[ROW] == "keine Blitze im Umkreis von 400 km"
    b = rows["storm-B01"]
    assert b.label is None
    assert b.li_km is not None
    assert 200 < b.li_km < 260
    assert dict(event_rows(b))[ROW].endswith("Richtung NW")
    c = rows["storm-C05"]  # imported: never checked
    assert (c.label, c.li_checked_at) == (None, None)
    assert dict(event_rows(c))[ROW] == "ungeprüft"


async def test_gaps_and_running_storms_stay_unchecked(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev, _, _ = await _camera(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    t0 = dt.datetime(2026, 10, 14, 22, 0, tzinfo=dt.UTC)
    store = FakeStore()
    store.add(t0, [])
    store.add(t0 + dt.timedelta(minutes=20), [])  # 22:10 .. 22:20 missing
    async with maker() as db:
        db.add_all([
            _flash(dev.device_id, t0 + dt.timedelta(minutes=2), "storm-A"),
            _flash(dev.device_id, t0 + dt.timedelta(minutes=12), "storm-A"),
        ])  # fmt: skip
        await db.commit()
        await lightning_li.ingest(db, store, READ)
        assert await lightning_li.check(db, NOW) == set()
    async with maker() as db:
        events = {e.start.minute: e for e in await db.scalars(select(EventRecord))}
    assert events[2].li_checked_at is not None
    assert events[12].li_checked_at is None  # in the gap
    assert events[2].label is None  # the storm is not fully checked

    # The gap filled later; a storm still going on (last flash < 30 min ago) waits.
    store.add(t0 + dt.timedelta(minutes=10), [])
    async with maker() as db:
        await lightning_li.ingest(db, store, READ)
        soon = t0 + dt.timedelta(minutes=30)
        assert await lightning_li.check(db, soon) == set()
        assert await lightning_li.check(db, NOW) == {dev.device_id}


async def test_the_owner_wins(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev, _, _ = await _camera(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    t0 = dt.datetime(2026, 10, 14, 22, 0, tzinfo=dt.UTC)
    store = FakeStore()
    store.add(t0, [])
    flash = _flash(dev.device_id, t0 + dt.timedelta(minutes=2), "storm-A")
    async with maker() as db:
        db.add(flash)
        await db.commit()
        await labels.set_on_hub(db, dev.device_id, NIGHT, flash.event_id, "confirmed")
        await db.commit()
        await lightning_li.ingest(db, store, READ)
        assert await lightning_li.check(db, NOW) == set()
    async with maker() as db:
        e = await db.scalar(select(EventRecord))
    assert e is not None
    assert (e.label, e.label_auto) == ("confirmed", False)
    assert e.li_checked_at is not None


async def test_old_flashes_and_products_are_dropped(app: FastAPI) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    t0 = NOW - dt.timedelta(hours=50)
    store = FakeStore()
    async with maker() as db:
        db.add(LiFlash(at=t0, lat=LAT, lon=LON))
        await db.commit()
        await lightning_li.ingest(db, store, NOW)
        assert await db.scalar(select(func.count()).select_from(LiFlash)) == 0
        assert not await lightning_li.covered(db, t0, t0 + dt.timedelta(minutes=1))


async def test_backfill_reads_further_back(app: FastAPI) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    store = FakeStore()
    store.add(NOW - dt.timedelta(hours=20), [(NOW - dt.timedelta(hours=20), LAT, LON)])
    store.add(NOW - dt.timedelta(hours=60), [(NOW - dt.timedelta(hours=60), LAT, LON)])
    async with maker() as db:
        assert await lightning_li.ingest(db, store, NOW) == 0  # the usual 3 h
        assert await lightning_li.ingest(db, store, NOW, dt.timedelta(hours=24)) == 1
        # Never beyond the 48 h the hub keeps.
        assert await lightning_li.ingest(db, store, NOW, dt.timedelta(hours=100)) == 0


async def test_data_store_requests() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if request.url.path.endswith("/os"):
            return httpx.Response(200, json={"totalResults": 0, "features": []})
        return httpx.Response(200, content=b"nc")

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    store = lightning_li.DataStore("key", "secret", client)
    assert await store.search(NOW - dt.timedelta(hours=1), NOW) == []
    g = lightning_li.Granule("P 1", NOW, NOW, "a_BODY_0001.nc")
    assert await store.body(g) == b"nc"
    await client.aclose()
    token, search, download = seen
    assert token.headers["authorization"].startswith("Basic ")
    assert search.url.params["pi"] == "EO:EUM:DAT:0691"
    assert search.url.params["dtstart"] == "2026-10-15T02:00:00Z"
    assert search.headers["authorization"] == "Bearer tok"
    assert (
        str(download.url)
        .split("?")[0]
        .endswith("/collections/EO%3AEUM%3ADAT%3A0691/products/P%201/entry")
    )
    assert download.url.params["name"] == "a_BODY_0001.nc"
