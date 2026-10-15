"""The app's sky view (roadmap N6): directions above the camera now."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.models import Device, EventRecord
from tests.test_app_manage import _camera  # pyright: ignore[reportPrivateUsage]


async def test_sky_view(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(client)
    url = f"/api/v1/cameras/{device_id}/sky-view"
    r = await client.get(url, headers=auth)
    assert r.status_code == 409  # no place yet
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    now = dt.datetime.now(dt.UTC)
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        device.last_status = {"settings": {"latitude": 48.31, "longitude": 14.29}}
        db.add(EventRecord(device_id=device_id, event_id="meteor-1", night_id="20261013",
                           kind="meteor", start=now - dt.timedelta(hours=1),
                           end=now - dt.timedelta(minutes=59), confidence=0.9,
                           has_image=False, data={"start_altitude_deg": 40.0,
                           "start_azimuth_deg": 100.0, "end_altitude_deg": 25.0,
                           "end_azimuth_deg": 120.0}))  # fmt: skip
        await db.commit()
    view = (await client.get(url, headers=auth)).json()
    assert view["lines"]
    assert {o["kind"] for o in view["objects"]} >= {"constellation"}
    assert view["meteors"] == [{"at": view["meteors"][0]["at"], "start": [40.0, 100.0],
                                "end": [25.0, 120.0]}]  # fmt: skip
