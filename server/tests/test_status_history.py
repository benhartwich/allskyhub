"""Charts of the camera's state (exposure, gain, temperatures, disk) and the 24-hour view."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Mode, Status
from allskyhub_server.models import StatusSample
from tests.fake_device import FakeDevice
from tests.ws_helpers import device_ws, paired_token, send


def _status(exposure_us: int) -> Status:
    return Status(
        mode=Mode.NIGHT, exposure_us=exposure_us, gain=250, mean=0.2, uptime_s=60,
        time_trusted=True, sensor_temp_c=12.5, cpu_temp_c=48, disk_free_pct=63,
    )  # fmt: skip


async def test_status_is_kept_every_five_minutes(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with device_ws(live_app, token) as ws:
        for exposure in (20_000_000, 25_000_000, 30_000_000):
            await send(ws, _status(exposure))
        for _ in range(100):
            async with maker() as db:
                if await db.scalar(select(func.count()).select_from(StatusSample)):
                    break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
    async with maker() as db:
        rows = list(await db.scalars(select(StatusSample)))
    assert len(rows) == 1  # one point per five minutes
    assert rows[0].exposure_us == 20_000_000
    assert rows[0].sensor_temp_c == 12.5

    page = (await client.get(f"/cameras/{dev.device_id}")).text
    # The viewer (charts.js) draws the last 24 hours, the camera's state included.
    assert 'data-default="24"' in page
    assert "data-status" in page
    data = (await client.get(f"/cameras/{dev.device_id}/charts/data", params={"hours": 24})).json()
    assert data["status"]["exposure"][-1] == 20.0

    night = dt.datetime.now(dt.UTC).astimezone(dt.timezone(dt.timedelta(hours=1)))
    night_id = (night - dt.timedelta(hours=12)).strftime("%Y%m%d")
    night_page = (await client.get(f"/cameras/{dev.device_id}/nights/{night_id}")).text
    assert f'data-default="{night_id}"' in night_page  # the viewer, camera state included
    assert "data-status" in night_page
