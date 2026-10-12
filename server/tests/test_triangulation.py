"""Meteor trajectory from two cameras (roadmap C4), with synthetic geometry."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import meteor_pairs
from allskyhub_server.devices import triangulation as t
from allskyhub_server.models import Device, EventRecord, MeteorPair
from tests.fake_device import FakeDevice
from tests.helpers import claim, make_account

LINZ = (48.30, 14.29)
SALZBURG = (47.80, 13.04)


def _sighting(cam: tuple[float, float], a: t.Vec, b: t.Vec, noise: float = 0.0) -> t.Sighting:
    az1, alt1 = t.look(cam[0], cam[1], 0.3, a)
    az2, alt2 = t.look(cam[0], cam[1], 0.3, b)
    return t.Sighting(cam[0], cam[1], 0.3, (az1 + noise, alt1), (az2, alt2 - noise))


def test_heights_come_back() -> None:
    """A meteor from 95 km down to 75 km over the Traunsee, seen from Linz and Salzburg."""
    top, bottom = t.ecef(47.95, 13.80, 95.0), t.ecef(47.80, 13.95, 75.0)
    trajectory = t.triangulate(_sighting(LINZ, top, bottom), _sighting(SALZBURG, bottom, top))
    assert trajectory is not None
    high, low = trajectory.points
    assert high.height_km == pytest.approx(95.0, abs=0.05)
    assert low.height_km == pytest.approx(75.0, abs=0.05)
    assert high.lat == pytest.approx(47.95, abs=0.001)
    assert low.lon == pytest.approx(13.95, abs=0.001)
    expected = t._norm(t._sub(top, bottom))  # pyright: ignore[reportPrivateUsage]
    assert trajectory.length_km == pytest.approx(expected, abs=0.05)
    assert trajectory.convergence_deg > 10


def test_small_errors_stay_small() -> None:
    """0.1° error in the directions (a good plate solve) moves the heights by about 1 km."""
    top, bottom = t.ecef(47.95, 13.80, 95.0), t.ecef(47.80, 13.95, 75.0)
    trajectory = t.triangulate(
        _sighting(LINZ, top, bottom, noise=0.1), _sighting(SALZBURG, top, bottom, noise=-0.1)
    )
    assert trajectory is not None
    assert trajectory.points[0].height_km == pytest.approx(95.0, abs=2.5)
    assert trajectory.points[1].height_km == pytest.approx(75.0, abs=2.5)


def test_not_the_same_meteor() -> None:
    """Two different meteors give no plausible height."""
    a = _sighting(LINZ, t.ecef(47.95, 13.80, 95.0), t.ecef(47.80, 13.95, 75.0))
    b = _sighting(SALZBURG, t.ecef(46.5, 16.0, 90.0), t.ecef(46.4, 16.2, 70.0))
    assert t.triangulate(a, b) is None


def test_geodetic_round_trip() -> None:
    lat, lon, h = t.geodetic(t.ecef(48.136, 14.39, 85.0))
    assert (lat, lon, h) == pytest.approx((48.136, 14.39, 85.0), abs=1e-6)


async def test_pairing_two_cameras(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """The same meteor from two cameras of one owner: paired, shown on both event pages;
    a camera of someone else pairs only when both owners show their location publicly."""
    top, bottom = t.ecef(47.95, 13.80, 95.0), t.ecef(47.80, 13.95, 75.0)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    start = dt.datetime(2026, 10, 13, 22, 0, tzinfo=dt.UTC)

    async def camera(c: httpx.AsyncClient, place: tuple[float, float], second: int) -> str:
        dev = FakeDevice()
        reg = await dev.register(c)
        assert reg.pairing_code
        await claim(c, reg.pairing_code, f"Kamera {place[0]}")
        async with maker() as db:
            await db.execute(
                update(Device)
                .where(Device.id == dev.device_id)
                .values(last_status={"settings": {"latitude": place[0], "longitude": place[1],
                                                  "elevation_m": 300}})
            )  # fmt: skip
            az1, alt1 = t.look(place[0], place[1], 0.3, top)
            az2, alt2 = t.look(place[0], place[1], 0.3, bottom)
            begin = start + dt.timedelta(seconds=second)
            event_id = f"meteor-20261013T2200{second:02d}Z"
            db.add(EventRecord(device_id=dev.device_id, event_id=event_id,
                               night_id="20261013", kind="meteor", start=begin,
                               end=begin + dt.timedelta(seconds=30), confidence=0.9,
                               has_image=False, data={"start_azimuth_deg": az1,
                               "start_altitude_deg": alt1, "end_azimuth_deg": az2,
                               "end_altitude_deg": alt2, "track_ordered": False}))  # fmt: skip
            await db.commit()
        return dev.device_id

    await make_account(client)
    linz = await camera(client, LINZ, 0)
    salzburg = await camera(client, SALZBURG, 20)  # exposures overlap by 10 s
    assert await meteor_pairs.pair_event(maker, linz, "meteor-20261013T220000Z") == 1
    page = (await client.get(f"/cameras/{linz}/events/20261013/meteor-20261013T220000Z")).text
    assert "Bahn des Meteors" in page
    assert "95 km → 75 km" in page
    assert f'href="/cameras/{salzburg}/events/20261013/meteor-20261013T220020Z"' in page
    other = (await client.get(f"/cameras/{salzburg}/events/20261013/meteor-20261013T220020Z")).text
    assert "Bahn des Meteors" in other

    # Someone else's camera with an overlapping meteor: only with public locations.
    await make_account(new_client)
    stranger = await camera(new_client, (47.6, 14.6), 5)
    async with maker() as db:
        assert len((await db.scalars(select(MeteorPair))).all()) == 1
    assert await meteor_pairs.pair_event(maker, stranger, "meteor-20261013T220005Z") == 0
