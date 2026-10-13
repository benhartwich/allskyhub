# pyright: reportPrivateUsage=false
"""Satellites among the meteor events (roadmap H6, SPEC §7.9)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import satellite_match as sm
from allskyhub_server.devices.satellites import (
    _look,
    _propagate,
)
from allskyhub_server.models import Device

ELEMENTS = """ISS (ZARYA)
1 25544U 98067A   26285.85396186  .00006018  00000+0  11791-3 0  9995
2 25544  51.6314  77.7958 0006705 255.2292 104.7953 15.48843879589996
STARLINK-1008
1 44714U 19074B   26285.53461193  .00044939  00000+0  42156-3 0  9995
2 44714  53.1456 218.9725 0002996 113.3562 246.7770 15.66755185382529
ISS (ZARYA)
1 25544U 98067A   26285.85396186  .00006018  00000+0  11791-3 0  9995
2 25544  51.6314  77.7958 0006705 255.2292 104.7953 15.48843879589996
"""
LAT, LON = 48.3, 14.29
# The ISS high in the south over Linz, in sunlight before dawn.
AT = dt.datetime(2026, 10, 13, 6, 3, 30, tzinfo=dt.UTC)


def _where(at: dt.datetime) -> tuple[float, float]:
    lines = ELEMENTS.splitlines()
    sat: Any = sm._SGP4.Satrec.twoline2rv(lines[1], lines[2])
    _, r = _propagate(sat, at)
    az, alt = _look(r, at, LAT, LON)
    return alt, az


def test_parse_and_build() -> None:
    entries = sm.parse(ELEMENTS + "garbage\n1 broken\n")
    assert [(n, k) for n, k, _, _ in entries] == [
        ("ISS (ZARYA)", 25544), ("STARLINK-1008", 44714), ("ISS (ZARYA)", 25544),
    ]  # fmt: skip
    catalogue = sm.build(entries)
    assert len(catalogue) == 2  # the same NORAD number once


def test_the_iss_is_named() -> None:
    catalogue = sm.build(sm.parse(ELEMENTS))
    start, end = AT, AT + dt.timedelta(seconds=4)
    ends = (_where(start), _where(end))
    found = sm.match(catalogue, LAT, LON, start, end, ends)
    assert found is not None
    assert (found.norad, found.detail) == (25544, "ISS")
    assert 400 < found.distance_km < 700

    # 2° to the side: not the ISS.
    off = ((ends[0][0] + 2, ends[0][1]), (ends[1][0] + 2, ends[1][1]))
    assert sm.match(catalogue, LAT, LON, start, end, off) is None
    # The right place, but a minute earlier: the ISS was elsewhere.
    early = start - dt.timedelta(minutes=1)
    assert sm.match(catalogue, LAT, LON, early, early + dt.timedelta(seconds=4), ends) is None
    # A streak much longer than the ISS's path in that time: not the ISS.
    far = (ends[0], (ends[1][0] - 15, ends[1][1]))
    assert sm.match(catalogue, LAT, LON, start, end, far) is None


def test_names_in_labels() -> None:
    assert sm.SatMatch("ISS (UNITY)", 25575, 1.0).detail == "ISS"
    assert sm.SatMatch("CSS (TIANHE)", 48274, 1.0).detail == "Tiangong"
    assert sm.SatMatch("STARLINK-1008", 44714, 1.0).detail == "STARLINK-1008 44714"


async def test_catalogue_is_fetched_once_a_day(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["GROUP"])
        return httpx.Response(200, text=ELEMENTS)

    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    catalogue = sm.SatelliteCatalogue(tmp_path, client)
    found = await catalogue.get()
    assert found is not None
    assert len(found) == 2
    assert calls == ["active", "visual"]
    assert await catalogue.get() is found
    # After a restart the elements come from disk.
    again = sm.SatelliteCatalogue(tmp_path, client)
    assert again is not catalogue
    assert len(await again.get() or []) == 2  # pyright: ignore[reportArgumentType]
    assert calls == ["active", "visual"]


class _Fixed:
    def __init__(self, catalogue: sm.Catalogue) -> None:
        self._catalogue = catalogue

    async def get(self) -> sm.Catalogue:
        return self._catalogue


async def test_a_meteor_that_is_the_iss_is_marked(app: FastAPI, client: httpx.AsyncClient) -> None:
    from allskyhub_protocol import EventLabel
    from allskyhub_server.devices import aircraft, labels
    from tests.test_aircraft_labels import _camera, _event
    from tests.test_web_events import NIGHT, record

    dev, _, _ = await _camera(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = {"settings": {"latitude": LAT, "longitude": LON}}
        (a_alt, a_az), (b_alt, b_az) = _where(AT), _where(AT + dt.timedelta(seconds=4))
        for event_id, extra in (
            ("meteor-20261013T060330Z", {}),
            ("meteor-20261013T060330Z-2", {"imported": True}),
            ("meteor-20261013T060330Z-3", {}),
        ):
            row = record(dev.device_id)
            row.event_id, row.start, row.end = event_id, AT, AT + dt.timedelta(seconds=4)
            row.data = {**row.data, "start_altitude_deg": a_alt, "start_azimuth_deg": a_az,
                        "end_altitude_deg": b_alt, "end_azimuth_deg": b_az, **extra}  # fmt: skip
            db.add(row)
        await db.commit()
    watch: aircraft.Watch = app.state.aircraft
    watch.satellites = _Fixed(sm.build(sm.parse(ELEMENTS)))  # pyright: ignore[reportAttributeAccessIssue]

    # The camera's own trail verdict (automatic) is replaced by the catalogue's name.
    async with maker() as db:
        trail = EventLabel(night_id=NIGHT, event_id="meteor-20261013T060330Z",
                           label="false_positive", reason="other", at=AT, auto=True,
                           detail="trail: satellite")  # fmt: skip
        assert await labels.from_device(db, dev.device_id, trail)
    assert await watch.check_new(dev.device_id, "meteor-20261013T060330Z")
    marked = await _event(app, dev.device_id, "meteor-20261013T060330Z")
    assert (marked.label_reason, marked.label_detail, marked.label_auto) == (
        "satellite",
        "ISS",
        True,
    )
    assert marked.label_pending
    assert not await watch.check_new(dev.device_id, "meteor-20261013T060330Z")  # said already

    assert not await watch.check_new(dev.device_id, "meteor-20261013T060330Z-2")  # imported
    # The owner's word stands.
    async with maker() as db:
        owner = EventLabel(night_id=NIGHT, event_id="meteor-20261013T060330Z-3",
                           label="confirmed", at=AT)  # fmt: skip
        assert await labels.from_device(db, dev.device_id, owner)
    assert not await watch.check_new(dev.device_id, "meteor-20261013T060330Z-3")
    assert (await _event(app, dev.device_id, "meteor-20261013T060330Z-3")).label == "confirmed"
