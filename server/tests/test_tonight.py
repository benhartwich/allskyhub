"""'Heute Nacht' (roadmap C1): sun, moon and the cloud forecast."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import astro, tonight
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

LAT, LON = 48.306, 14.29  # Linz
VIENNA = ZoneInfo("Europe/Vienna")


def _local(t: dt.datetime | None) -> str:
    assert t is not None
    return t.astimezone(VIENNA).strftime("%d. %H:%M")


def test_night_times_and_moon() -> None:
    """Regression values for 12/13 October 2026; the sun agrees with the agent's sun code
    (SPEC §4.2) to 0.01°, the moon with the lunation (new 10 Oct, full 26 Oct)."""
    noon = dt.datetime(2026, 10, 12, 10, tzinfo=dt.UTC)
    n = astro.night_after(noon, LAT, LON)
    assert _local(n.sunset) == "12. 18:20"
    assert _local(n.dusk) == "12. 20:03"
    assert _local(n.dawn) == "13. 05:35"
    assert _local(n.sunrise) == "13. 07:19"
    assert n.moon_waxing
    assert n.moon_illumination < 0.1  # new moon on 10 October
    full = astro.moon_illumination(dt.datetime(2026, 10, 26, tzinfo=dt.UTC))
    assert full > 0.99


def test_no_dark_night_in_the_arctic_summer() -> None:
    n = astro.night_after(dt.datetime(2026, 6, 21, 10, tzinfo=dt.UTC), 69.6, 18.9)
    assert n.dusk is None
    assert n.dawn is None
    t = tonight.build(dt.datetime(2026, 6, 21, 12, tzinfo=dt.UTC), 69.6, 18.9, VIENNA, [])
    assert t.summary == "Heute wird es nicht astronomisch dunkel."


def _forecast(clouds: dict[int, int], day: int = 12) -> list[tonight.Hour]:
    start = dt.datetime(2026, 10, day, 12, tzinfo=dt.UTC)
    return [
        tonight.Hour(start + dt.timedelta(hours=i), clouds.get(i, 90), 0, 0, 0, None)
        for i in range(24)
    ]


def test_best_window_and_summary() -> None:
    now = dt.datetime(2026, 10, 12, 15, tzinfo=dt.UTC)
    # Clear from 21 UTC (hour 9) to 02 UTC, cloudy otherwise; the moon is new.
    clear = dict.fromkeys(range(9, 14), 10)
    t = tonight.build(now, LAT, LON, VIENNA, _forecast(clear))
    assert t.best is not None
    assert t.summary == "Klar und dunkel von 23:00 bis 04:00 Uhr."
    assert [h.good for h in t.hours].count(True) == 5
    assert t.fmt(None) == "–"
    assert t.moon_phase == "zunehmende Sichel"

    cloudy = tonight.build(now, LAT, LON, VIENNA, _forecast({}))
    assert cloudy.best is None
    assert cloudy.summary == "Bewölkt, die ganze Nacht."
    assert tonight.build(now, LAT, LON, VIENNA, []).summary == "Keine Wolkenvorhersage verfügbar."


def test_bright_moon_spoils_clear_hours() -> None:
    """Near full moon (26 October) clear hours with the moon up are not good."""
    now = dt.datetime(2026, 10, 26, 15, tzinfo=dt.UTC)
    t = tonight.build(now, LAT, LON, VIENNA, _forecast(dict.fromkeys(range(24), 0), day=26))
    assert t.night.moon_illumination > 0.9
    assert all(not h.good for h in t.hours if h.moon_up)
    assert any(h.moon_up for h in t.hours)


def test_after_midnight_it_is_still_the_same_night() -> None:
    before = tonight.build(dt.datetime(2026, 10, 12, 20, tzinfo=dt.UTC), LAT, LON, VIENNA, [])
    after = tonight.build(dt.datetime(2026, 10, 13, 1, tzinfo=dt.UTC), LAT, LON, VIENNA, [])
    assert before.night.dusk == after.night.dusk
    later = tonight.build(dt.datetime(2026, 10, 13, 9, tzinfo=dt.UTC), LAT, LON, VIENNA, [])
    assert later.night.dusk != before.night.dusk


def test_parse_forecast_is_tolerant() -> None:
    hours = tonight.parse_forecast(
        {"hourly": {"time": [1791763200, 1791766800, "x"], "cloud_cover": [12.4, None, 3],
                    "cloud_cover_low": [1]}}
    )  # fmt: skip
    assert [(h.clouds, h.low) for h in hours] == [(12, 1)]
    assert tonight.parse_forecast({"nope": 1}) == []
    assert tonight.parse_forecast([]) == []
    assert tonight.zone_for("Nowhere/City").key == "Europe/Berlin"


async def test_camera_and_public_page_show_tonight(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "Heute Nacht" not in page  # no location reported yet
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(
                last_status={"settings": {"latitude": LAT, "longitude": LON,
                                          "timezone": "Europe/Vienna"}}
            )
        )  # fmt: skip
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "Heute Nacht" in page
    assert "Klar und dunkel von" in page  # the test forecast is clear
    assert "night-bars" in page
