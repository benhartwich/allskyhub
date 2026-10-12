"""App API: overview (health, tonight, last night) and measurement series."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.models import Device, SkySample, StatusSample
from tests.fake_device import FakeDevice
from tests.helpers import claim, make_account
from tests.test_app_api import _app_login  # pyright: ignore[reportPrivateUsage]


async def _camera(app: FastAPI, client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    dev = FakeDevice()
    reg = await dev.register(client)
    assert reg.pairing_code
    email = await make_account(client)
    await claim(client, reg.pairing_code, "Garten")
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    now = dt.datetime.now(dt.UTC)
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = {
            "settings": {"latitude": 48.14, "longitude": 14.39, "timezone": "Europe/Vienna"}
        }
        for i in range(10):
            at = now - dt.timedelta(minutes=i)
            db.add(SkySample(device_id=dev.device_id, night_id="20261014", at=at,
                             cloud_cover=0.2, sqm_mag=21.0, stars=250))  # fmt: skip
            db.add(StatusSample(device_id=dev.device_id, at=at, exposure_us=10_000_000, gain=200))
        await db.commit()
    return dev.device_id, await _app_login(client, email)


async def test_overview(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(app, client)
    r = await client.get(f"/api/v1/cameras/{device_id}/overview", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["online"] is False
    assert any(item["key"] for item in body["health"])  # offline is a problem
    assert body["tonight"] is not None
    assert body["tonight"]["moon_phase"]
    assert "summary" in body["tonight"]


async def test_series(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(app, client)
    r = await client.get(f"/api/v1/cameras/{device_id}/series?hours=24", headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["sky"]["sqm"]) == 10
    assert data["status"]["exposure"][0] == 10.0
    assert data["tz"] == "Europe/Vienna"
    assert "ephemeris" in data


async def test_other_account_gets_404(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, _ = await _camera(app, client)
    other = await make_account(client)
    auth = await _app_login(client, other)
    assert (
        await client.get(f"/api/v1/cameras/{device_id}/overview", headers=auth)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/cameras/{device_id}/series", headers=auth)
    ).status_code == 404
