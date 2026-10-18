"""Aircraft by day (roadmap H9, SPEC §7.8 By day)."""

from __future__ import annotations

import datetime as dt
import itertools
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import aircraft, day_aircraft
from allskyhub_server.devices.aircraft import Fix, Tracks
from allskyhub_server.models import AircraftPass, Device, Frame
from tests.fake_device import FakeDevice
from tests.helpers import home_csrf
from tests.test_sky_overlay import _status  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import paired_token

LAT, LON = 50.04, 10.39
BERLIN = ZoneInfo("Europe/Berlin")
NOON = dt.datetime(2026, 10, 14, 11, 0, tzinfo=dt.UTC)  # 13:00 at the camera
STEP_DEG = 0.0216  # 2.4 km per 10 s: about 470 kn


def _southbound(tracks: Tracks, hex_: str, start: dt.datetime, lat0: float, minutes: int) -> None:
    """An aircraft at 10 km flying due south over the camera, a position every 10 s."""
    for i in range(minutes * 6):
        at = start + dt.timedelta(seconds=10 * i)
        tracks.add(
            "adsb.lol",
            hex_,
            Fix(at, lat0 - STEP_DEG * i, LON, 10_000.0, 180.0, 470.0, "DLH4AB", "D-AIUA", "A320"),
        )


async def _passes(app: FastAPI, device_id: str) -> list[AircraftPass]:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        rows = await db.scalars(
            select(AircraftPass)
            .where(AircraftPass.device_id == device_id)
            .order_by(AircraftPass.start)
        )
        return list(rows)


async def _camera(app: FastAPI, client: httpx.AsyncClient, **values: Any) -> str:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device is not None
        device.last_status = {
            **_status(),
            "settings": {"latitude": LAT, "longitude": LON, "timezone": "Europe/Berlin"},
        }
        for key, value in values.items():
            setattr(device, key, value)
        await db.commit()
    return dev.device_id


def test_registration_and_type_from_adsb_lol() -> None:
    body = {
        "now": NOON.timestamp() * 1000,
        "ac": [{"hex": "3C6444", "flight": "DLH4AB ", "r": "D-AIUA", "t": "A320",
                "alt_baro": 33000, "lat": 50.1, "lon": 10.4}],
    }  # fmt: skip
    tracks = Tracks()
    for hex_, fix in aircraft.parse(body, NOON):
        tracks.add("adsb.lol", hex_, fix)
    assert tracks.info == {"3c6444": ("D-AIUA", "A320")}
    tracks.prune(NOON + dt.timedelta(hours=1))
    assert tracks.info == {}


def test_only_by_day_and_when_on() -> None:
    status = {"settings": {"latitude": LAT, "longitude": LON}}
    assert aircraft.wants_day(status, True, NOON) == (LAT, LON)
    assert aircraft.wants_day(status, False, NOON) is None
    assert aircraft.wants_day(status, True, NOON + dt.timedelta(hours=11)) is None  # night
    assert aircraft.wants_day({}, True, NOON) is None  # no place


async def test_a_pass_overhead_is_kept_once(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    tracks = Tracks()
    # Over the camera at NOON + 4 min; above 20° for about 3 minutes around it.
    _southbound(tracks, "3c6444", NOON, LAT + 0.52, 9)
    # Far east all the time: never 20° up.
    for i in range(54):
        at = NOON + dt.timedelta(seconds=10 * i)
        tracks.add("adsb.lol", "4b1805", Fix(at, LAT, LON + 1.2, 11_000.0, 90.0, 450.0, "SWR1"))
    passes = day_aircraft.Passes(app.state.sessionmaker)
    t = NOON
    while t <= NOON + dt.timedelta(minutes=12):
        await passes.update(tracks, [(device_id, LAT, LON, BERLIN)], t)
        t += dt.timedelta(seconds=30)
    [found] = await _passes(app, device_id)
    assert found.night_id == "20261014"
    assert (found.icao, found.callsign, found.registration, found.type_code) == (
        "3c6444", "DLH4AB", "D-AIUA", "A320",
    )  # fmt: skip
    assert found.top_alt_deg > 80
    assert found.start_alt_deg >= day_aircraft.MIN_ALT
    assert found.end_alt_deg >= day_aircraft.MIN_ALT
    assert found.start_az_deg < 10 or found.start_az_deg > 350  # comes from the north
    assert 170 < found.end_az_deg < 190  # leaves to the south
    assert found.min_km < 2
    assert found.alt_m == 10_000
    assert dt.timedelta(minutes=2) < found.end - found.start < dt.timedelta(minutes=5)
    assert passes.open == {}


async def test_one_position_is_no_pass(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    tracks = Tracks()
    tracks.add("adsb.lol", "3c6444", Fix(NOON, LAT, LON, 10_000.0, None, None, "DLH4AB"))
    passes = day_aircraft.Passes(app.state.sessionmaker)
    for minutes in (0, 3):
        await passes.update(
            tracks, [(device_id, LAT, LON, BERLIN)], NOON + dt.timedelta(minutes=minutes)
        )
    assert await _passes(app, device_id) == []


async def test_watch_fetches_by_day_every_third_round(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    on = await _camera(app, client)
    off = await _camera(app, client, day_aircraft=False)
    asked: list[tuple[float, float, int]] = []

    async def fetch(lat: float, lon: float, nm: int) -> dict[str, Any]:
        asked.append((lat, lon, nm))
        return {"now": NOON.timestamp() * 1000, "ac": []}

    watch = aircraft.Watch(app.state.sessionmaker, app.state.connections, fetch)

    def online() -> list[str]:
        return [on, off]

    monkeypatch.setattr(app.state.connections, "online_ids", online)
    for i in range(6):
        await watch.round(NOON + dt.timedelta(seconds=10 * i))
    assert len(asked) == 2  # rounds 0 and 3; the camera that switched it off asks nothing
    assert asked[0][:2] == (50.0, 10.4)  # rounded to 0.1°


async def test_pages(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    top = NOON + dt.timedelta(minutes=4)
    async with maker() as db:
        found = AircraftPass(
            device_id=device_id, night_id="20261014", icao="3c6444", callsign="DLH4AB",
            registration="D-AIUA", type_code="A320", start=top - dt.timedelta(minutes=2),
            end=top + dt.timedelta(minutes=2), top_at=top, top_alt_deg=84.2, top_az_deg=95.0,
            start_alt_deg=21.0, start_az_deg=2.0, end_alt_deg=22.5, end_az_deg=181.0,
            alt_m=10_000, min_km=1.1,
        )  # fmt: skip
        db.add(found)
        db.add(Frame(device_id=device_id, night_id="20261013", name="20261014T110330Z.jpg",
                     captured_at=top - dt.timedelta(seconds=30), mode="day", exposure_us=100,
                     gain=0, mean=0.4, sun_elevation=30.0, has_thumb=True))  # fmt: skip
        await db.commit()
        pass_id = found.id

    page = (await client.get(f"/cameras/{device_id}/ueberfluege?nacht=20261014")).text
    assert "Am Tag: 1 Flugzeug" in page
    assert f"/cameras/{device_id}/flugzeuge/{pass_id}" in page
    assert "84° O" in page
    assert "Aircraft data: adsb.lol (ODbL)" in page
    # A day without meteors is still in the list of nights.
    assert "Keine Überflüge und Meteore" not in page

    night = (await client.get(f"/cameras/{device_id}/nights/20261014")).text
    assert "1 Flugzeug am Tag" in night

    detail = await client.get(f"/cameras/{device_id}/flugzeuge/{pass_id}")
    assert detail.status_code == 200
    assert "/frames/20261013/20261014T110330Z.jpg/thumb.jpg" in detail.text
    assert 'class="air-trail" d="M' in detail.text  # drawn with the lens
    assert "showTrace=2026-10-14" in detail.text
    assert (await client.get(f"/cameras/{device_id}/flugzeuge/nope")).status_code == 404

    r = await client.post(
        f"/cameras/{device_id}/day-aircraft", data={"csrf_token": await home_csrf(client)}
    )
    assert r.status_code == 303
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        assert device.day_aircraft is False


async def test_backs_off_while_adsb_lol_throttles(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC §7.8: after 429 or 5xx the wait doubles up to 5 minutes, then back to 10 s."""
    camera = await _camera(app, client, day_aircraft=True)
    answers = [429, 503, 429, 200]
    asked: list[dt.datetime] = []
    now = NOON

    async def fetch(lat: float, lon: float, nm: int) -> dict[str, Any]:
        asked.append(now)
        code = answers.pop(0) if answers else 200
        if code != 200:
            request = httpx.Request("GET", "https://api.adsb.lol/")
            raise httpx.HTTPStatusError("busy", request=request, response=httpx.Response(code))
        return {"now": now.timestamp() * 1000, "ac": []}

    watch = aircraft.Watch(app.state.sessionmaker, app.state.connections, fetch)
    monkeypatch.setattr(app.state.connections, "online_ids", lambda: [camera])

    def watched(device_id: str, at: dt.datetime) -> bool:
        return True

    monkeypatch.setattr(watch, "_is_watched", watched)
    while now < NOON + dt.timedelta(seconds=180):
        await watch.round(now)
        now += dt.timedelta(seconds=10)
    waits = [int((b - a).total_seconds()) for a, b in itertools.pairwise(asked)]
    assert waits[:4] == [20, 40, 80, 10]
