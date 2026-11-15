"""Weather balloons over the live image (draft B, devices/radiosondes.py)."""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import radiosondes, sky_layers
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.test_public_page import _public  # pyright: ignore[reportPrivateUsage]
from tests.test_sky_overlay import AT, LENS, _status  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import paired_token

LAT, LON = 50.04, 10.39  # _status()'s made-up place

# SondeHub's /sondes answer (shape of api.v2.sondehub.org, values made up): one balloon
# 100 km north at 30 km, one heard an hour ago, one broken.
ANSWER = {
    "X1234567": {
        "serial": "X1234567", "type": "RS41", "subtype": "RS41-SG",
        "datetime": (AT - dt.timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
        "lat": LAT + 0.8993, "lon": LON, "alt": 30000.0, "vel_h": 10.0, "vel_v": 5.0,
        "heading": 90.0, "frequency": 403.5,
    },
    "OLD": {
        "serial": "OLD", "type": "M20", "datetime": (AT - dt.timedelta(hours=1)).isoformat(),
        "lat": LAT, "lon": LON, "alt": 12000.0,
    },
    "BROKEN": {"serial": "BROKEN", "lat": "x"},
}  # fmt: skip


def test_cell_is_coarse() -> None:
    assert radiosondes.cell(50.04, 10.39) == (50.0, 10.5)
    assert radiosondes.cell(-33.87, 151.21) == (-34.0, 151.0)


def test_parse_and_look() -> None:
    sondes = {s.serial: s for s in radiosondes.parse(ANSWER)}
    assert set(sondes) == {"X1234567", "OLD"}
    rs41 = sondes["X1234567"]
    assert rs41.kind == "RS41-SG"
    assert radiosondes.now_at(sondes["OLD"], AT) is None  # silent for an hour
    where = radiosondes.now_at(rs41, AT)
    assert where is not None
    assert where[2] == 30000.0 + 5.0 * 30  # carried on for 30 s
    alt, az, km = radiosondes.look(LAT, LON, 300.0, where)
    assert abs(az) < 1.0 or abs(az - 360) < 1.0  # north
    assert 100 < km < 106
    # atan(29.7 / 100) less the Earth's curvature over 100 km (about 0.45°).
    assert 15.5 < alt < 16.6
    overhead = radiosondes.look(LAT, LON, 0.0, (LAT, LON, 20000.0))
    assert overhead[0] > 89.9
    assert radiosondes.parse([1, 2]) == []


def test_balloon_layer() -> None:
    lay = sky_layers.build(_status(), AT, None, radiosondes.parse(ANSWER))
    assert lay is not None
    [balloon] = lay.balloons
    assert balloon.text.startswith("Wetterballon · 30,1 km ↑ · 10")
    assert balloon.text.endswith("km entfernt")
    assert balloon.y < LENS.cy  # north is up
    assert balloon.trail.startswith("M")
    empty = sky_layers.build(_status(), AT)
    assert empty is not None
    assert empty.balloons == []


async def test_only_for_the_cameras_own_viewers(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        device.last_status = _status()
        device.last_frame = {"captured_at": AT.isoformat()}
        device.latest_image_at = AT
        await db.commit()
    cache: radiosondes.Radiosondes = app.state.radiosondes
    cache.put(LAT, LON, radiosondes.parse(ANSWER))
    own = (await client.get(f"/cameras/{device_id}/live")).text
    assert 'data-overlay-toggle="balloons"' in own
    assert "Wetterballon · 30,1 km" in own
    assert "SondeHub (CC BY-SA 2.0)" in own
    public = (await new_client.get(f"/sky/{slug}/now")).text
    assert "Wetterballon" not in public
    assert 'data-overlay-toggle="balloons"' not in public


async def test_no_balloons_without_an_answer(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = {**_status(), "settings": {"latitude": -33.87, "longitude": 151.21}}
        device.last_frame = {"captured_at": AT.isoformat()}
        device.latest_image_at = AT
        await db.commit()
    live = (await client.get(f"/cameras/{dev.device_id}/live")).text
    assert "Wetterballon ·" not in live
    assert "SondeHub" not in live


def test_trail_ends_at_the_marker_of_an_older_balloon() -> None:
    """Heard 8 minutes ago: the marker is carried on 90 s only, and the trail with it
    (allsky review of #462)."""
    old = radiosondes.Sonde(
        serial="Y1", kind="RS41", at=AT - dt.timedelta(minutes=8), lat=LAT + 0.9, lon=LON,
        alt_m=25000.0, vel_h=20.0, vel_v=5.0, heading=0.0,
    )  # fmt: skip
    lay = sky_layers.build(_status(), AT, None, [old])
    assert lay is not None
    [balloon] = lay.balloons
    start = balloon.trail[1:].split("L")[0].split()
    assert (round(float(start[0])), round(float(start[1]))) == (round(balloon.x), round(balloon.y))
    assert radiosondes.ahead(old, AT) == 90.0


async def test_asks_for_the_last_minutes_and_backs_off() -> None:
    asked: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(429, headers={"Retry-After": "300"})

    cache = radiosondes.Radiosondes(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    cache.warm(LAT, LON)
    await asyncio.gather(*cache._tasks)  # pyright: ignore[reportPrivateUsage]
    assert asked[0].url.params["last"] == "600"
    assert asked[0].url.params["lat"] == "50.0"
    # Retry-After (300 s), not the first backoff step (120 s), sets the wait.
    wait = cache._not_before[radiosondes.cell(LAT, LON)] - time.monotonic()  # pyright: ignore[reportPrivateUsage]
    assert 200 < wait <= 300
    cache.warm(LAT, LON)  # not again yet
    assert not cache._tasks  # pyright: ignore[reportPrivateUsage]
    assert len(asked) == 1
    await cache.close()
