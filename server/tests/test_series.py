"""Measurement viewer data (devices/series.py, charts.js): gaps, averaging, ranges, access."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import series, tonight
from allskyhub_server.models import Device, NightStat, SkySample, StatusSample
from tests.fake_device import FakeDevice
from tests.helpers import claim, home_csrf, make_account

T0 = dt.datetime(2026, 10, 8, 20, tzinfo=dt.UTC)


@dataclass
class Row:
    at: dt.datetime
    cloud_cover: float | None = 0.5
    sqm_mag: float | None = 21.0
    stars: int | None = 100


def test_pause_becomes_a_gap() -> None:
    rows = [Row(T0), Row(T0 + dt.timedelta(minutes=1)), Row(T0 + dt.timedelta(hours=2))]
    cols = series.columns(rows, series.SKY_FIELDS, 0)
    assert cols["sqm"] == [21.0, 21.0, None, 21.0]
    assert cols["cloud"][0] == 50.0
    t1 = cols["t"][1]
    assert t1 is not None
    assert cols["t"][2] == t1 + 1


def test_buckets_average_and_skip_missing() -> None:
    rows = [
        Row(T0, sqm_mag=20.0),
        Row(T0 + dt.timedelta(minutes=1), sqm_mag=None),
        Row(T0 + dt.timedelta(minutes=2), sqm_mag=21.0),
    ]
    cols = series.columns(rows, series.SKY_FIELDS, 600)
    assert cols["sqm"] == [20.5]
    assert len(cols["t"]) == 1


def test_night_range_is_local_noon_to_noon() -> None:
    start, end = series.night_range("20261008", tonight.zone_for("Europe/Vienna"))
    assert start.astimezone(dt.UTC) == dt.datetime(2026, 10, 8, 10, tzinfo=dt.UTC)
    assert end - start == dt.timedelta(days=1)


def test_ephemeris_has_night_at_midnight() -> None:
    eph = series.ephemeris(T0, T0 + dt.timedelta(hours=6), 48.1, 14.4)
    assert len(eph["t"]) == 73  # every 5 minutes, both ends
    sun = eph["sun"]
    assert all(v is not None and v < -18 for v in sun[24:48])  # 22:00-24:00 UTC in October
    illumination = eph["moon_illumination"][0]
    assert illumination is not None
    assert 0 <= illumination <= 1


def test_public_place_is_rounded() -> None:
    device = Device(last_status={"settings": {"latitude": 48.307, "longitude": 14.287}})
    assert series.location(device, public=True) == (48.5, 14.5)
    assert series.location(device, public=False) == (48.307, 14.287)


async def _camera(app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient) -> str:
    await make_account(client)
    dev = FakeDevice()
    reg = await dev.register(new_client)
    assert reg.pairing_code
    await claim(client, reg.pairing_code, "Sternwarte Nord")
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    now = dt.datetime.now(dt.UTC)
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(
                last_status={
                    "settings": {"latitude": 48.31, "longitude": 14.29, "timezone": "Europe/Vienna"}
                }
            )
        )
        for i in range(30):
            at = now - dt.timedelta(minutes=i)
            db.add(SkySample(device_id=dev.device_id, night_id="20261008", at=at,
                             cloud_cover=0.1, sqm_mag=21.2, stars=300))  # fmt: skip
            db.add(StatusSample(device_id=dev.device_id, at=at, exposure_us=20_000_000,
                                gain=250, cpu_temp_c=45.0))  # fmt: skip
        db.add(NightStat(device_id=dev.device_id, night_id="20261008", clear_hours=5.5,
                         best_sqm=21.3, meteors=4))  # fmt: skip
        await db.commit()
    return dev.device_id


async def test_owner_sees_sky_and_camera_state(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id = await _camera(app, client, new_client)
    assert "/charts" in (await client.get(f"/cameras/{device_id}")).text
    page = await client.get(f"/cameras/{device_id}/charts")
    assert page.status_code == 200
    assert "charts.js" in page.text

    r = await client.get(f"/cameras/{device_id}/charts/data", params={"hours": 24})
    data = r.json()
    assert data["hours"] == 24
    assert data["tz"] == "Europe/Vienna"
    assert len(data["sky"]["t"]) == 30
    assert data["status"]["exposure"][0] == 20.0
    assert "ephemeris" in data

    night = (await client.get(f"/cameras/{device_id}/charts/data")).json()
    assert night["night"] is not None  # default: the newest night with sky data

    year = (await client.get(f"/cameras/{device_id}/charts/nights", params={"year": 2026})).json()
    assert year["nights"]["best_sqm"] == [21.3]

    anon = await new_client.get(f"/cameras/{device_id}/charts/data")
    assert anon.status_code in (303, 401, 404)
    bad = await client.get(f"/cameras/{device_id}/charts/data", params={"night": "../x"})
    assert bad.status_code == 422


async def test_public_viewer_has_no_camera_internals(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id = await _camera(app, client, new_client)
    r = await client.post(
        f"/cameras/{device_id}/public",
        data={"enabled": "true", "csrf_token": await home_csrf(client)},
    )
    assert r.status_code == 303
    page = (await client.get(f"/cameras/{device_id}")).text
    match = re.search(r'href="/sky/([A-Za-z0-9_-]+)"', page)
    assert match
    slug = match[1]

    assert (await new_client.get(f"/sky/{slug}/charts")).status_code == 200
    data = (await new_client.get(f"/sky/{slug}/charts/data", params={"hours": 24})).json()
    assert "status" not in data
    assert len(data["sky"]["sqm"]) == 30

    # The owner can hide the sky values; then the viewer is gone too.
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        device.public_profile = {**(device.public_profile or {}), "sections": {"sky": False}}
        await db.commit()
    assert (await new_client.get(f"/sky/{slug}/charts")).status_code == 404
    assert (await new_client.get(f"/sky/{slug}/charts/data")).status_code == 404
