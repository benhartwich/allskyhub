"""Where meteorites land with the winds aloft (fireball package C)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import dark_flight, fireball, winds
from allskyhub_server.models import Device, MeteorPair
from tests.fake_device import FakeDevice
from tests.helpers import claim, home_csrf, make_account

# A GFS cut around the Traunsee from NOMADS (run 2026-11-05 00 UTC + 6 h), 22 levels.
GRIB = (Path(__file__).parent / "data" / "gfs_traunsee.grb2").read_bytes()
HIGH, LOW = (47.95, 13.80, 80.0), (47.80, 13.95, 25.0)


def test_standard_atmosphere() -> None:
    assert dark_flight.air(0.0)[0] == pytest.approx(1.225, rel=0.01)
    assert dark_flight.air(11.0)[0] == pytest.approx(0.364, rel=0.02)
    assert dark_flight.air(30.0)[0] == pytest.approx(0.0184, rel=0.05)
    assert dark_flight.air(0.0)[1] == pytest.approx(340.3, rel=0.01)


def test_heavy_pieces_fly_farther_and_wind_carries_light_ones() -> None:
    calm = dark_flight.strewn_field(HIGH, LOW, [])
    by_mass = {x.mass_kg: x for x in calm.landings if x.speed_kms == 2.5}
    assert by_mass[0.01].km_from_end < by_mass[10.0].km_from_end < 30
    heading = fireball.bearing_deg(HIGH[0], HIGH[1], LOW[0], LOW[1])
    assert by_mass[10.0].azimuth_deg == pytest.approx(heading, abs=3)
    # 30 m/s from the west at every height: the light piece goes east.
    westerly = [(0.0, 30.0, 0.0), (50.0, 30.0, 0.0)]
    windy = {x.mass_kg: x for x in dark_flight.strewn_field(HIGH, LOW, westerly).landings}
    assert 45 < windy[0.01].azimuth_deg < 135
    assert windy[0.01].km_from_end > by_mass[0.01].km_from_end + 5
    assert len(calm.outline) >= 4
    assert calm.outline[0] == calm.outline[-1]


def test_runs_before_the_meteor_that_are_out() -> None:
    at = dt.datetime(2026, 11, 5, 21, 40, tzinfo=dt.UTC)
    soon = winds.runs(at, at)
    assert soon[0] == (dt.datetime(2026, 11, 5, 12, tzinfo=dt.UTC), 10)  # 18 UTC not out yet
    later = winds.runs(at, at + dt.timedelta(hours=5))
    assert later[0] == (dt.datetime(2026, 11, 5, 18, tzinfo=dt.UTC), 4)
    assert "gfs.20261105%2F18" in winds.url(*later[0], 47.8, 13.95)


def test_profile_from_gfs() -> None:
    rows = winds.profile(GRIB, LOW[0], LOW[1])
    assert len(rows) == 22
    heights = [h for h, _, _ in rows]
    assert heights == sorted(heights)
    assert heights[-1] > 40  # 1 hPa, above any end of a glow
    assert all(abs(u) < 150 and abs(v) < 150 for _, u, v in rows)


async def test_winds_for_a_fireball_and_its_page(app: FastAPI, client: httpx.AsyncClient) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    await make_account(client)
    ids: list[str] = []
    for place in ("Linz", "Salzburg"):
        dev = FakeDevice()
        reg = await dev.register(client)
        assert reg.pairing_code
        await claim(client, reg.pairing_code, place)
        ids.append(dev.device_id)
    for device_id in ids:
        data = {"csrf_token": await home_csrf(client), "network": "on"}
        assert (await client.post(f"/cameras/{device_id}/network", data=data)).status_code == 303
    at = dt.datetime(2026, 11, 5, 6, 0, tzinfo=dt.UTC)
    async with maker() as db:
        a, b = sorted(ids)
        pair = MeteorPair(
            device_a=a, event_a="meteor-1", device_b=b, event_b="meteor-2", at=at,
            high_lat=HIGH[0], high_lon=HIGH[1], high_km=HIGH[2],
            low_lat=LOW[0], low_lon=LOW[1], low_km=LOW[2],
            length_km=60.0, convergence_deg=40.0, peak=1.0, low_err_km=0.5,
        )  # fmt: skip
        db.add(pair)
        await db.commit()
        pair_id = pair.id
    asked: list[str] = []

    def nomads(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, content=GRIB)

    async with httpx.AsyncClient(transport=httpx.MockTransport(nomads)) as fake:
        async with maker() as db:
            assert await winds.round_once(db, fake, at + dt.timedelta(hours=5)) == 1
        async with maker() as db:
            assert await winds.round_once(db, fake, at + dt.timedelta(hours=6)) == 0  # kept
    assert len(asked) == 1
    assert "file=gfs.t06z.pgrb2.0p25.f000" in asked[0]
    async with maker() as db:
        stored = await db.scalar(select(MeteorPair).where(MeteorPair.id == pair_id))
        assert stored is not None
        assert stored.wind is not None
        assert stored.wind["source"] == winds.SOURCE
        assert stored.strewn is not None
        assert len(stored.strewn["landings"]) == 8
        assert await db.get(Device, a) is not None

    page = (await client.get(f"/feuerkugeln/{pair_id}")).text
    assert "Mit dem Wind in der Höhe (NOAA GFS 0,25°, Lauf 2026-11-05 06 UTC + 0 h)" in page
    assert "<dt>10 kg</dt>" in page
    assert '"landings": [[' in page
