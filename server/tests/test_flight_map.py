"""The aircraft around a camera on a map (draft 7, devices/flight_map.py). Made-up place
NORDHEIM (tests use no real places)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update

from allskyhub_server.devices import aircraft, flight_map
from allskyhub_server.devices.aircraft import Fix, Tracks
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

NORDHEIM = (48.30, 4.29)
NOW = dt.datetime(2026, 11, 13, 20, 0, tzinfo=dt.UTC)


def _fix(at: dt.datetime, lat: float, lon: float, alt_m: float = 10_000.0) -> Fix:
    return Fix(at, lat, lon, alt_m, 90.0, 450.0, "TST123")


def test_rings_at_cruising_height() -> None:
    lat, lon = NORDHEIM
    ten, thirty = flight_map.rings(lat, lon)
    # Flat Earth would give 56.7 and 17.3 km; the curvature brings the 10° ring in a bit.
    assert 50 <= ten.km <= 57
    assert 16.5 <= thirty.km <= 17.5
    up, _, _ = aircraft.look(lat, lon, lat + ten.km / 111.195, lon, 10_000.0)
    assert abs(up - 10.0) < 0.05


def test_planes_in_the_picture_first_with_trails() -> None:
    lat, lon = NORDHEIM
    tracks = Tracks()
    # Overhead-ish (10 km north: about 45° up), far (100 km north: low), and one without height.
    for i in range(9):
        t = NOW - dt.timedelta(seconds=120 - 15 * i)
        tracks.add("adsb.lol", "aaa111", _fix(t, lat + 0.09, lon - 0.01 * (8 - i)))
        tracks.add("adsb.lol", "bbb222", _fix(t, lat + 0.9, lon))
    tracks.add("adsb.lol", "ccc333", Fix(NOW, lat, lon, None, None, None, "NOALT"))
    found = flight_map.planes(tracks, lat, lon, NOW)
    assert [p.hex for p in found] == ["aaa111", "bbb222"]
    near, far = found
    assert near.in_picture
    assert 40 < near.elevation < 50
    assert not far.in_picture
    assert len(near.trail) >= 8
    assert near.trail[-1] == (near.lat, near.lon)
    # Outside RADIUS_KM: left out.
    tracks.add("adsb.lol", "ddd444", _fix(NOW, lat + 2.0, lon))
    assert "ddd444" not in {p.hex for p in flight_map.planes(tracks, lat, lon, NOW)}


async def test_page_and_data(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    page = await client.get(f"/cameras/{dev.device_id}/flugzeuge")
    assert page.status_code == 200
    assert "Standort noch nicht gemeldet" in page.text
    lat, lon = NORDHEIM
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"settings": {"latitude": lat, "longitude": lon}})
        )
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}/flugzeuge")).text
    assert 'id="flight-map"' in page
    assert '"rings"' in page
    watch = aircraft.current
    assert watch is not None
    now = dt.datetime.now(dt.UTC)
    watch.tracks.add("adsb.lol", "eee555", _fix(now, lat + 0.09, lon))
    data = (await client.get(f"/cameras/{dev.device_id}/flugzeuge.json")).json()
    [plane] = [p for p in data["planes"] if p["hex"] == "eee555"]
    assert plane["in_picture"] is True
    assert plane["alt_km"] == 10.0
    assert data["credit"] == aircraft.CREDIT
    assert (
        'href="/cameras/' + dev.device_id + '/flugzeuge"'
        in (await client.get(f"/cameras/{dev.device_id}")).text
    )
    # Without a session (another visitor) the data stays closed.
    async with httpx.AsyncClient(
        transport=client._transport,  # pyright: ignore[reportPrivateUsage]
        base_url=str(client.base_url),
    ) as other:
        answer = await other.get(f"/cameras/{dev.device_id}/flugzeuge.json")
    assert answer.status_code in (303, 401, 404)
