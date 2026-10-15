"""Meteor showers live from the network (roadmap N3)."""

from __future__ import annotations

import datetime as dt
import math

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import showers
from allskyhub_server.devices import triangulation as t
from allskyhub_server.models import Device, EventRecord, MeteorPair, SkySample
from allskyhub_sky import sidereal_deg
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token


def test_active_and_upcoming() -> None:
    assert [s.key for s in showers.active(dt.date(2026, 10, 14))] == ["Orionids"]
    assert [s.key for s in showers.active(dt.date(2026, 12, 14))] == ["Geminids"]
    assert [s.key for s in showers.active(dt.date(2027, 1, 3))] == ["Quadrantids"]
    assert showers.upcoming(dt.date(2026, 9, 1)).key == "Orionids"
    assert showers.days_to_peak(showers.BY_KEY["Orionids"], dt.date(2026, 10, 14)) == 7
    ra, dec = showers.radiant(showers.BY_KEY["Orionids"], dt.date(2026, 10, 14))
    assert (ra, dec) == pytest.approx((95.0 - 4.9, 16.0 - 0.7))


def _pair_from(radiant: tuple[float, float], at: dt.datetime) -> MeteorPair:
    """A meteor over Linz coming from ``radiant`` (RA, Dec) at ``at``."""
    ra, dec = (math.radians(v) for v in radiant)
    gmst = math.radians(sidereal_deg(at, 0.0))
    xs, ys, z = math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)
    x = xs * math.cos(gmst) + ys * math.sin(gmst)
    y = -xs * math.sin(gmst) + ys * math.cos(gmst)
    low = t.ecef(48.3, 10.3, 80.0)
    high = t.geodetic((low[0] + 40 * x, low[1] + 40 * y, low[2] + 40 * z))
    lat, lon, km = t.geodetic(low)
    return MeteorPair(at=at, high_lat=high[0], high_lon=high[1], high_km=high[2],
                      low_lat=lat, low_lon=lon, low_km=km, length_km=40.0,
                      convergence_deg=40.0)  # fmt: skip


def test_radiant_from_a_path() -> None:
    at = dt.datetime(2026, 10, 21, 2, 0, tzinfo=dt.UTC)
    ra, dec = showers.pair_radiant(_pair_from((95.0, 16.0), at))
    assert showers.separation((ra, dec), (95.0, 16.0)) < 0.01
    mean = showers.mean_radiant([(359.0, 10.0), (1.0, 12.0)])
    assert mean is not None
    assert showers.separation(mean, (0.0, 11.0)) < 0.01


async def test_page_counts_network_cameras(app: FastAPI, client: httpx.AsyncClient) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    # Half past an hour, so 5 to 13 minutes back stay in this hour and 65 in the one before.
    now = dt.datetime.now(dt.UTC).replace(minute=30, second=0, microsecond=0)
    ids: list[str] = []
    for network in (True, True, False):
        dev = FakeDevice()
        await paired_token(client, dev)
        ids.append(dev.device_id)
        async with maker() as db:
            device = await db.get(Device, dev.device_id)
            assert device
            device.network = network
            for minutes in (5, 65):
                at = now - dt.timedelta(minutes=minutes)
                db.add(SkySample(device_id=dev.device_id, night_id="20261013", at=at,
                                 cloud_cover=0.1))  # fmt: skip
            for n in range(3):
                start = now - dt.timedelta(minutes=10 + n)
                db.add(EventRecord(device_id=dev.device_id, event_id=f"meteor-{n}",
                                   night_id="20261013", kind="meteor", start=start,
                                   end=start + dt.timedelta(seconds=30), confidence=0.9,
                                   has_image=False, data={"shower": "Orionids",
                                   "shower_match": "radiant" if n else "date"}))  # fmt: skip
            await db.commit()
    async with maker() as db:
        live = await showers.live(db, now)
    assert live.cameras == 2
    last = live.hours[-1]
    assert (last.meteors, last.by_shower, last.clear_cameras) == (6, {"Orionids": 4}, 2)
    assert last.rate() == 3.0
    assert live.hours[-2].clear_cameras == 2

    page = await client.get("/meteorstroeme")
    assert page.status_code == 200
    assert "2 Kameras im Kameranetz" in page.text
