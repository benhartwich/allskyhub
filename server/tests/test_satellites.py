"""Satellite passes (roadmap C6), checked against Skyfield for the same element set."""

from __future__ import annotations

import datetime as dt

from fastapi import FastAPI

from allskyhub_server.devices import satellites
from tests.conftest import ISS_TLE

LAT, LON = 50.036, 10.39


def test_iss_passes_match_skyfield() -> None:
    """Skyfield (DE421, same TLE) puts the culminations of the visible morning passes at
    15 Oct 04:29:26 UTC (14°), 17 Oct 04:31:13 (30°) and 19 Oct 04:33:08 (59°)."""
    start = dt.datetime(2026, 10, 15, 0, tzinfo=dt.UTC)
    found = satellites.passes({25544: ISS_TLE}, LAT, LON, start, start + dt.timedelta(days=5))
    tops = {(p.start + (p.end - p.start) / 2).strftime("%d"): round(p.max_altitude) for p in found}
    assert tops["15"] == 14
    assert tops["17"] == 30
    by_day = [p for p in found if p.start.day == 19 and p.max_altitude > 50]
    assert len(by_day) == 1
    assert abs(by_day[0].max_altitude - 59) <= 1
    first = found[0]
    assert first.name == "Raumstation ISS"
    assert dt.datetime(2026, 10, 15, 4, 26, tzinfo=dt.UTC) <= first.start
    assert first.end <= dt.datetime(2026, 10, 15, 4, 34, tzinfo=dt.UTC)


def test_no_passes_in_daylight() -> None:
    noon = dt.datetime(2026, 10, 15, 9, tzinfo=dt.UTC)
    assert satellites.passes({25544: ISS_TLE}, LAT, LON, noon, noon + dt.timedelta(hours=4)) == []


async def test_tles_come_from_the_cache(app: FastAPI) -> None:
    tles = await app.state.tles.get()
    assert tles == {25544: ISS_TLE}  # the other satellites answer 404 in the test
