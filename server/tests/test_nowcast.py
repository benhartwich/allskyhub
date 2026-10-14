"""Clouds in the next hour from the cameras (roadmap N4)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import fireball, nowcast
from allskyhub_server.devices.tonight import ForecastCache, Hour, parse_forecast
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

NOW = dt.datetime(2026, 10, 14, 22, 30, tzinfo=dt.UTC)
HERE = (48.30, 14.29)


def _hours(kmh: float, wind_from: float) -> list[Hour]:
    at = NOW.replace(minute=0)
    return [Hour(at, 50, 0, 0, 0, None, kmh, wind_from)]


def _fill(
    history: nowcast.History, device_id: str, cover: float, cells: list[float | None]
) -> None:
    for minutes in (8, 4, 0):
        at = NOW - dt.timedelta(minutes=minutes)
        history.add(device_id, {"cloud_cover": cover, "cloud_map": cells}, at)


def test_forecast_parses_the_wind() -> None:
    payload = {"hourly": {"time": [0], "cloud_cover": [10], "wind_speed_700hPa": [42.5],
                          "wind_direction_700hPa": [270]}}  # fmt: skip
    hour = parse_forecast(payload)[0]
    assert (hour.wind_kmh, hour.wind_from) == (42.5, 270.0)


def test_median_drops_single_bad_frames() -> None:
    history = nowcast.History()
    for minutes, cover in ((6, 0.9), (4, 0.1), (2, 0.9), (30, 0.0)):
        history.add("cam", {"cloud_cover": cover}, NOW - dt.timedelta(minutes=minutes))
    history.add("cam", {"cloud_cover": 0.9}, NOW)
    assert history.cover("cam", NOW) == 0.9
    assert history.cells("cam", NOW) == []


def test_gap_upwind_at_a_neighbour() -> None:
    """Cloudy here, clear 20 km to the west, wind from the west at 40 km/h: clear in
    about 30 minutes."""
    history = nowcast.History()
    _fill(history, "here", 0.9, [0.9] * 24)
    west = fireball.destination(*HERE, 270, 20)
    cast = nowcast.forecast(history, "here", HERE, _hours(40, 270), [(west, 0.05)], NOW)
    assert cast is not None
    assert [s.minutes for s in cast.steps if s.cover < 0.3] == [30]
    assert cast.summary == "Es klart in etwa 30 Minuten auf."
    assert cast.wind_text == "Wind aus Westen, 40 km/h"
    # A neighbour downwind (east) says nothing about what comes.
    east = fireball.destination(*HERE, 90, 20)
    cast = nowcast.forecast(history, "here", HERE, _hours(40, 270), [(east, 0.05)], NOW)
    assert cast is not None
    assert cast.summary == "Keine Lücke in Sicht."


def test_clouds_in_the_own_map_upwind() -> None:
    """Clear overhead, but the low band to the north is full: with a north wind of 30 km/h
    those clouds (about 6 km away) arrive in about 12 minutes, shown as a quarter hour."""
    history = nowcast.History()
    cells: list[float | None] = [0.0] * 24
    cells[0] = 1.0  # low band, north
    _fill(history, "here", 0.1, cells)
    cast = nowcast.forecast(history, "here", HERE, _hours(30, 0), [], NOW)
    assert cast is not None
    # The middle band (clear, about 2 km away) first, then the low band.
    assert [(s.minutes, s.cover) for s in cast.steps] == [(5, 0.0), (15, 1.0)]
    assert cast.steps[1].label == "am eigenen Himmel"
    assert cast.summary == "Wolken in etwa 15 Minuten."


def test_no_wind_no_measurement() -> None:
    history = nowcast.History()
    assert nowcast.forecast(history, "here", HERE, _hours(30, 0), [], NOW) is None
    _fill(history, "here", 0.5, [])
    assert nowcast.forecast(history, "here", HERE, [], [], NOW) is None
    calm = nowcast.forecast(history, "here", HERE, _hours(2, 0), [], NOW)
    assert calm is not None
    assert calm.steps == []


async def test_camera_page_and_app(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = {"settings": {"latitude": HERE[0], "longitude": HERE[1]}}
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "Die nächste Stunde" not in page  # no sky measurement yet
    now = dt.datetime.now(dt.UTC)
    nowcast.HISTORY.add(dev.device_id, {"cloud_cover": 0.8}, now)
    # The test forecast has no wind: still nothing.
    assert "Die nächste Stunde" not in (await client.get(f"/cameras/{dev.device_id}")).text

    def windy(request: httpx.Request) -> httpx.Response:
        start = now.replace(minute=0, second=0, microsecond=0)
        times = [int((start + dt.timedelta(hours=i)).timestamp()) for i in range(3)]
        return httpx.Response(200, json={"hourly": {
            "time": times, "cloud_cover": [80] * 3,
            "wind_speed_700hPa": [40] * 3, "wind_direction_700hPa": [270] * 3}})  # fmt: skip

    app.state.forecast = ForecastCache(httpx.AsyncClient(transport=httpx.MockTransport(windy)))
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "Die nächste Stunde" in page
    assert "jetzt 80 % Wolken" in page
    assert "mit dem Kameranetz kämen" in page
