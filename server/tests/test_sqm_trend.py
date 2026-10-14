"""Sky brightness over the years (roadmap N5)."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import sqm_trend
from allskyhub_server.models import NightStat
from allskyhub_sky import astro
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token


def _dark_nights(year: int, month: int) -> list[str]:
    """Evenings of the month whose midnight has little moon."""
    out: list[str] = []
    day = dt.date(year, month, 1)
    while day.month == month:
        midnight = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.UTC)
        if astro.moon_illumination(midnight) <= 0.2:
            out.append(day.strftime("%Y%m%d"))
        day += dt.timedelta(days=1)
    return out


def _nights(per_year: float, months: int = 24) -> list[NightStat]:
    nights: list[NightStat] = []
    for i in range(months):
        year, month = 2024 + i // 12, i % 12 + 1
        value = 21.5 + per_year * i / 12
        for night_id in _dark_nights(year, month)[:3]:
            nights.append(NightStat(device_id="d", night_id=night_id, clear_hours=5.0,
                                    best_sqm=value, meteors=0))  # fmt: skip
    # A moonlit night and a cloudy one count nowhere.
    nights.append(NightStat(device_id="d", night_id="20240101", clear_hours=0.2, best_sqm=10.0))
    return nights


def test_brighter_sky_gives_a_negative_trend() -> None:
    months = sqm_trend.monthly(_nights(-0.2))
    assert len(months) == 24
    assert sqm_trend.slope(months) == pytest.approx(-0.2)
    trend = sqm_trend.Trend(months, sqm_trend.slope(months))
    assert trend.text == "Der Himmel wird heller: 0,20 mag/″² pro Jahr mehr Lichtverschmutzung."


def test_steady_and_too_short() -> None:
    months = sqm_trend.monthly(_nights(0.0))
    assert sqm_trend.Trend(months, sqm_trend.slope(months)).text.startswith("Der Himmel ist gleich")
    short = sqm_trend.monthly(_nights(-0.2, months=8))
    assert sqm_trend.slope(short) is None
    assert "mindestens ein Jahr" in sqm_trend.Trend(short, None).text


def test_moonlit_nights_are_left_out() -> None:
    full = "20240124"  # the moon is full on 25 January 2024
    bright = NightStat(device_id="d", night_id=full, clear_hours=6.0, best_sqm=18.0)
    also = NightStat(device_id="d", night_id=full, clear_hours=6.0, best_sqm=18.5)
    assert sqm_trend.monthly([bright, also]) == []


async def test_stats_page_and_app(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        for n in _nights(-0.2):
            n.device_id = dev.device_id
            db.add(n)
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}/stats")).text
    assert "Himmelshelligkeit über die Jahre" in page
    assert "Der Himmel wird heller: 0,20" in page
