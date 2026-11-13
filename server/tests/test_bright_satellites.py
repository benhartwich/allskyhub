"""The bright satellites over a camera (draft 5, devices/bright_satellites.py), with the ISS
element set that test_satellites.py checks against Skyfield."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update

from allskyhub_server.devices import bright_satellites, satellite_match
from allskyhub_server.models import Device
from allskyhub_server.settings import Settings
from tests.conftest import ISS_TLE
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

LAT, LON = 50.036, 10.39
# Skyfield: the ISS culminates at 59° on 19 Oct 2026 04:33:08 UTC, sunlit before dawn.
TOP = dt.datetime(2026, 10, 19, 4, 33, 8, tzinfo=dt.UTC)
TLE_TEXT = "ISS (ZARYA)\n" + "\n".join(ISS_TLE) + "\n"


def _catalogue() -> satellite_match.Catalogue:
    return satellite_match.build(satellite_match.parse(TLE_TEXT))


def test_names() -> None:
    assert bright_satellites.german_name(25544, "ISS (ZARYA)") == "Raumstation ISS"
    assert bright_satellites.german_name(1, "SL-16 R/B") == "SL-16 R/B (Raketenstufe)"
    assert bright_satellites.german_name(2, "FENGYUN 1C DEB") == "FENGYUN 1C DEB (Trümmerteil)"
    assert bright_satellites.german_name(3, "SEASAT 1") == "SEASAT 1"


def test_now_at_the_top_of_a_pass() -> None:
    [iss] = bright_satellites.now(_catalogue(), LAT, LON, TOP)
    assert iss.name == "Raumstation ISS"
    assert abs(iss.altitude - 59) <= 1
    assert 380 <= iss.height_km <= 460
    assert iss.sunlit
    assert iss.naked_eye
    assert iss.in_picture


def test_now_below_the_horizon_and_in_daylight() -> None:
    assert bright_satellites.now(_catalogue(), LAT, LON, TOP + dt.timedelta(minutes=30)) == []
    # Up in daylight (09–17 UTC): in the picture, but never for the naked eye.
    cat = _catalogue()
    t = dt.datetime(2026, 10, 15, 9, tzinfo=dt.UTC)
    times = [t + dt.timedelta(minutes=m) for m in range(0, 8 * 60, 2)]
    up = [s for at in times for s in bright_satellites.now(cat, LAT, LON, at) if s.in_picture]
    assert up
    assert not any(s.naked_eye for s in up)
    assert bright_satellites.now(None, LAT, LON, TOP) == []


def test_night_passes() -> None:
    start = dt.datetime(2026, 10, 19, 0, tzinfo=dt.UTC)
    [p] = bright_satellites.night_passes(
        _catalogue(), LAT, LON, start, start + dt.timedelta(hours=6)
    )
    assert p.name == "Raumstation ISS"
    assert abs(p.top_altitude - 59) <= 1
    assert p.start <= TOP <= p.end
    # 15 Oct: the visible pass only reaches 14°, below the 30° of the list.
    low = dt.datetime(2026, 10, 15, 0, tzinfo=dt.UTC)
    assert (
        bright_satellites.night_passes(_catalogue(), LAT, LON, low, low + dt.timedelta(hours=6))
        == []
    )
    assert bright_satellites.night_passes(_catalogue(), LAT, LON, start, start) == []


def test_location() -> None:
    assert bright_satellites.location({"settings": {"latitude": 50.0, "longitude": 10}}) == (
        50.0,
        10.0,
    )
    assert bright_satellites.location({"settings": {"latitude": True, "longitude": 10}}) is None
    assert bright_satellites.location({"settings": {}}) is None
    assert bright_satellites.location(None) is None


async def test_fragment(app: FastAPI, client: httpx.AsyncClient, settings: Settings) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    url = f"/cameras/{dev.device_id}/satelliten"
    assert "Standort noch nicht gemeldet" in (await client.get(url)).text
    assert (await client.get(url, params={"f": "morgen"})).status_code == 422
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"settings": {"latitude": LAT, "longitude": LON}})
        )
        await db.commit()
    assert "Bahndaten" in (await client.get(url)).text  # no elements on disk yet
    path = settings.data_dir / "gp" / "visual.tle"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TLE_TEXT)
    try:
        app.state.bright_satellites = satellite_match.SatelliteCatalogue(
            settings.data_dir / "gp", None, groups=("visual",)
        )
        for f in ("jetzt", "oben", "nacht"):
            page = (await client.get(url, params={"f": f})).text
            assert 'aria-pressed="true"' in page
            assert "Bahndaten" not in page
        assert 'hx-trigger="every 60s"' in (await client.get(url, params={"f": "oben"})).text
        assert "every 60s" not in (await client.get(url, params={"f": "nacht"})).text
        assert 'id="satelliten"' in (await client.get(f"/cameras/{dev.device_id}")).text
    finally:
        path.unlink()
