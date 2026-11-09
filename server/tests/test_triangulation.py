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
from tests.helpers import claim, home_csrf, make_account

NORDHEIM = (48.30, 4.29)
SUEDTAL = (47.80, 3.04)


def _sighting(cam: tuple[float, float], a: t.Vec, b: t.Vec, noise: float = 0.0) -> t.Sighting:
    az1, alt1 = t.look(cam[0], cam[1], 0.3, a)
    az2, alt2 = t.look(cam[0], cam[1], 0.3, b)
    return t.Sighting(cam[0], cam[1], 0.3, (az1 + noise, alt1), (az2, alt2 - noise))


def test_heights_come_back() -> None:
    """A meteor from 95 km down to 75 km over a lake, seen from Nordheim and Südtal."""
    top, bottom = t.ecef(47.95, 3.80, 95.0), t.ecef(47.80, 3.95, 75.0)
    trajectory = t.triangulate(_sighting(NORDHEIM, top, bottom), _sighting(SUEDTAL, bottom, top))
    assert trajectory is not None
    high, low = trajectory.points
    assert high.height_km == pytest.approx(95.0, abs=0.05)
    assert low.height_km == pytest.approx(75.0, abs=0.05)
    assert high.lat == pytest.approx(47.95, abs=0.001)
    assert low.lon == pytest.approx(3.95, abs=0.001)
    expected = t._norm(t._sub(top, bottom))  # pyright: ignore[reportPrivateUsage]
    assert trajectory.length_km == pytest.approx(expected, abs=0.05)
    assert trajectory.convergence_deg > 10


def test_small_errors_stay_small() -> None:
    """0.1° error in the directions (a good plate solve) moves the heights by about 1 km."""
    top, bottom = t.ecef(47.95, 3.80, 95.0), t.ecef(47.80, 3.95, 75.0)
    trajectory = t.triangulate(
        _sighting(NORDHEIM, top, bottom, noise=0.1), _sighting(SUEDTAL, top, bottom, noise=-0.1)
    )
    assert trajectory is not None
    assert trajectory.points[0].height_km == pytest.approx(95.0, abs=2.5)
    assert trajectory.points[1].height_km == pytest.approx(75.0, abs=2.5)


def test_not_the_same_meteor() -> None:
    """Two different meteors give no plausible height."""
    a = _sighting(NORDHEIM, t.ecef(47.95, 3.80, 95.0), t.ecef(47.80, 3.95, 75.0))
    b = _sighting(SUEDTAL, t.ecef(46.5, 6.0, 90.0), t.ecef(46.4, 6.2, 70.0))
    assert t.triangulate(a, b) is None


def test_geodetic_round_trip() -> None:
    lat, lon, h = t.geodetic(t.ecef(50.036, 10.39, 85.0))
    assert (lat, lon, h) == pytest.approx((50.036, 10.39, 85.0), abs=1e-6)


async def test_pairing_two_cameras(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """The same meteor from two cameras of one owner: paired, shown on both event pages;
    a camera of someone else pairs only when both owners joined the network (audit L2)."""
    top, bottom = t.ecef(47.95, 3.80, 95.0), t.ecef(47.80, 3.95, 75.0)
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
    nordheim = await camera(client, NORDHEIM, 0)
    salzburg = await camera(client, SUEDTAL, 20)  # exposures overlap by 10 s
    assert await meteor_pairs.pair_event(maker, nordheim, "meteor-20261013T220000Z") == 1
    page = (await client.get(f"/cameras/{nordheim}/events/20261013/meteor-20261013T220000Z")).text
    assert "Bahn des Meteors" in page
    assert "95 km → 75 km" in page
    assert f'href="/cameras/{salzburg}/events/20261013/meteor-20261013T220020Z"' in page
    other = (await client.get(f"/cameras/{salzburg}/events/20261013/meteor-20261013T220020Z")).text
    assert "Bahn des Meteors" in other

    # Someone else's camera with an overlapping meteor: only when both owners joined.
    await make_account(new_client)
    stranger = await camera(new_client, (47.6, 4.6), 5)
    async with maker() as db:
        assert len((await db.scalars(select(MeteorPair))).all()) == 1
    assert await meteor_pairs.pair_event(maker, stranger, "meteor-20261013T220005Z") == 0

    async def join(c: httpx.AsyncClient, device_id: str, on: bool) -> None:
        data = {"csrf_token": await home_csrf(c)} | ({"network": "on"} if on else {})
        assert (await c.post(f"/cameras/{device_id}/network", data=data)).status_code == 303

    await join(new_client, stranger, True)
    assert await meteor_pairs.pair_event(maker, stranger, "meteor-20261013T220005Z") == 0
    for device_id in (nordheim, salzburg):
        await join(client, device_id, True)
    assert await meteor_pairs.pair_event(maker, stranger, "meteor-20261013T220005Z") == 2
    page = (
        await new_client.get(f"/cameras/{stranger}/events/20261013/meteor-20261013T220005Z")
    ).text
    # Both pairs share the solution of all three cameras: one card naming both others.
    assert page.count("Bahn des Meteors") == 1
    assert "Aus allen Blickwinkeln" in page

    # Leaving the network takes the pairs off the other owner's pages.
    for device_id in (nordheim, salzburg):
        await join(client, device_id, False)
    page = (
        await new_client.get(f"/cameras/{stranger}/events/20261013/meteor-20261013T220005Z")
    ).text
    assert "Bahn des Meteors" not in page


BERGDORF = (47.57, 4.24)
TOP, BOTTOM = t.ecef(47.95, 3.80, 95.0), t.ecef(47.80, 3.95, 30.0)


def test_solved_together_with_an_error() -> None:
    """All cameras at once give the same heights; the error comes from their plate solves
    and shrinks with a third camera."""
    two = [_sighting(NORDHEIM, TOP, BOTTOM), _sighting(SUEDTAL, BOTTOM, TOP)]
    solution = t.solve(two, seed="x")
    assert solution is not None
    high, low = solution.trajectory.points
    assert (high.height_km, low.height_km) == pytest.approx((95.0, 30.0), abs=0.05)
    assert solution.cameras == 2
    assert solution.low_err_km is not None
    assert solution.high_err_km is not None
    assert 0.1 < solution.low_err_km < 5.0  # 0.3° at 50–100 km
    assert t.solve(two, seed="x") == solution  # repeatable

    three = t.solve([*two, _sighting(BERGDORF, TOP, BOTTOM)], seed="x")
    assert three is not None
    assert three.cameras == 3
    assert three.low_err_km is not None
    assert three.low_err_km < solution.low_err_km
    assert three.trajectory.points[1].height_km == pytest.approx(30.0, abs=0.05)

    rough = [t.Sighting(s.lat, s.lon, s.height_km, s.end1, s.end2, sigma_deg=1.0) for s in two]
    worse = t.solve(rough, seed="x")
    assert worse is not None
    assert worse.low_err_km is not None
    assert worse.low_err_km > 2 * solution.low_err_km


def test_one_camera_is_no_solution() -> None:
    assert t.solve([_sighting(NORDHEIM, TOP, BOTTOM)]) is None


async def _camera(
    client: httpx.AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    place: tuple[float, float],
    second: int,
    rms: float,
    ends: tuple[t.Vec, t.Vec] = (TOP, BOTTOM),
) -> str:
    """A camera of the client's owner with one meteor between ``ends``."""
    start = dt.datetime(2026, 11, 5, 21, 0, tzinfo=dt.UTC)
    dev = FakeDevice()
    reg = await dev.register(client)
    assert reg.pairing_code
    await claim(client, reg.pairing_code, f"Kamera {place[0]}")
    az1, alt1 = t.look(place[0], place[1], 0.3, ends[0])
    az2, alt2 = t.look(place[0], place[1], 0.3, ends[1])
    begin = start + dt.timedelta(seconds=second)
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"settings": {"latitude": place[0], "longitude": place[1],
                                              "elevation_m": 300},
                                 "orientation": {"rms_deg": rms}})
        )  # fmt: skip
        db.add(EventRecord(device_id=dev.device_id, event_id=f"meteor-{second}",
                           night_id="20261105", kind="meteor", start=begin,
                           end=begin + dt.timedelta(seconds=30), confidence=0.9,
                           has_image=False, data={"start_azimuth_deg": az1,
                           "start_altitude_deg": alt1, "end_azimuth_deg": az2,
                           "end_altitude_deg": alt2, "track_ordered": False,
                           "peak": 1.0}))  # fmt: skip
        await db.commit()
    return dev.device_id


async def test_three_cameras_one_fireball(app: FastAPI, client: httpx.AsyncClient) -> None:
    """A third camera of the same owner: all three pairs keep the solution of all three,
    and the fireball page names them and the error."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    await make_account(client)
    nordheim = await _camera(client, maker, NORDHEIM, 0, 0.2)
    salzburg = await _camera(client, maker, SUEDTAL, 10, 0.4)
    assert await meteor_pairs.pair_event(maker, nordheim, "meteor-0") == 1
    async with maker() as db:
        first = (await db.scalars(select(MeteorPair))).one()
        assert sorted(first.cameras) == sorted([nordheim, salzburg])
        assert first.low_err_km is not None
        two_err = first.low_err_km
    liezen = await _camera(client, maker, BERGDORF, 5, 0.2)
    assert await meteor_pairs.pair_event(maker, liezen, "meteor-5") == 2
    async with maker() as db:
        pairs = (await db.scalars(select(MeteorPair))).all()
    assert len(pairs) == 3
    assert all(sorted(p.cameras) == sorted([nordheim, salzburg, liezen]) for p in pairs)
    assert {round(p.low_km, 6) for p in pairs} == {round(pairs[0].low_km, 6)}  # one solution
    assert pairs[0].low_err_km is not None
    assert pairs[0].low_err_km < two_err
    assert pairs[0].low_km == pytest.approx(30.0, abs=0.05)

    page = (await client.get(f"/feuerkugeln/{pairs[0].id}")).text
    assert "Genauigkeit" in page
    assert "Ende ± " in page
    assert page.count("Kamera 4") == 3  # Kamera 48.3, 47.8, 47.57: the owner's own names


async def test_the_joint_end_decides_the_alarm(app: FastAPI, client: httpx.AsyncClient) -> None:
    """A rough camera alone would put the end below the meteorite line; all three
    together put it above, and no alarm goes out (allsky's review of #382: the filter
    reads the pairs fresh, not the pairwise values the session may still hold)."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    await make_account(client)
    top, end = t.ecef(47.95, 3.80, 95.0), t.ecef(47.80, 3.95, 38.0)
    deeper = t._add(top, t._mul(t._sub(end, top), 62 / 57))  # pyright: ignore[reportPrivateUsage]
    assert t.geodetic(deeper)[2] < 34.0
    await _camera(client, maker, NORDHEIM, 0, 0.2, (top, end))
    await _camera(client, maker, SUEDTAL, 10, 0.2, (top, end))
    liezen = await _camera(client, maker, BERGDORF, 5, 0.5, (top, deeper))
    alarms: list[object] = []

    async def alarm(pair_id: object) -> None:
        alarms.append(pair_id)

    assert await meteor_pairs.pair_event(maker, liezen, "meteor-5", on_fireball=alarm) == 2
    async with maker() as db:
        pairs = (await db.scalars(select(MeteorPair))).all()
    assert len(pairs) == 2
    assert all(p.low_km > 35.0 for p in pairs)
    assert alarms == []


def test_a_camera_reporting_nan_is_not_used() -> None:
    """``rms_deg`` and track ends come from the camera; JSON lets NaN through."""
    device = Device(id="x", last_status={"settings": {"latitude": 48.3, "longitude": 4.29},
                                          "orientation": {"rms_deg": float("nan")}})  # fmt: skip
    data = {"start_azimuth_deg": 10.0, "start_altitude_deg": 40.0,
            "end_azimuth_deg": 20.0, "end_altitude_deg": 30.0}  # fmt: skip
    event = EventRecord(event_id="meteor-1", kind="meteor", data=data)
    seen = meteor_pairs.sighting(device, event)
    assert seen is not None
    assert seen.sigma_deg == t.DEFAULT_SIGMA_DEG
    device.last_status = {"settings": {"latitude": 48.3, "longitude": 4.29},
                          "orientation": {"rms_deg": 90.0}}  # fmt: skip
    seen = meteor_pairs.sighting(device, event)
    assert seen is not None
    assert seen.sigma_deg == t.MAX_SIGMA_DEG
    event.data = {**data, "end_altitude_deg": float("nan")}
    assert meteor_pairs.sighting(device, event) is None
