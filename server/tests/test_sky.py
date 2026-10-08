"""Sky measurements from status.sky (SPEC §6.3): stored per frame, charted per night."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select

from allskyhub_protocol import Mode, SkyMetrics, Status
from allskyhub_server import maintenance
from allskyhub_server.devices.sky_chart import charts
from allskyhub_server.models import SkySample
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, home_csrf, owner_email
from tests.ws_helpers import device_ws, paired_token, send

NIGHT = "20261008"
T0 = dt.datetime(2026, 10, 8, 19, 0, tzinfo=dt.UTC)


def status(minute: int, clouds: float | None, sqm: float | None, stars: int | None) -> Status:
    return Status(
        mode=Mode.NIGHT, exposure_us=20_000_000, gain=100, mean=0.2, uptime_s=60,
        time_trusted=True,
        sky=SkyMetrics(at=T0 + dt.timedelta(minutes=minute), night_id=NIGHT,
                       cloud_cover=clouds, sqm_mag=sqm, stars=stars),
    )  # fmt: skip


async def test_sky_samples_stored_charted_and_purged(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, status(0, None, None, None))  # dusk: nothing to judge
        await send(ws, status(30, 0.9, 18.8, 40))
        await send(ws, status(30, 0.9, 18.8, 40))  # repeated until the next frame
        await send(ws, status(60, 0.05, 20.8, 900))
        await asyncio.sleep(0.3)

    async with app.state.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(SkySample)) == 3

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    api = f"/api/v1/cameras/{dev.device_id}"
    sky = (await client.get(f"{api}/nights/{NIGHT}/sky", headers=auth)).json()
    assert [s["cloud_cover"] for s in sky] == [None, 0.9, 0.05]
    nights = (await client.get(f"{api}/nights", headers=auth)).json()
    assert nights[0]["sky"] == 3

    page = (await client.get(f"/cameras/{dev.device_id}/nights/{NIGHT}")).text
    assert "Himmel in dieser Nacht" in page
    assert page.count('class="sky-chart') == 3
    assert "zuletzt 20.80 mag" in page
    assert "Himmelsdaten" in (await client.get(f"/cameras/{dev.device_id}/gallery")).text

    async with app.state.sessionmaker() as db:
        later = dt.datetime.now(dt.UTC) + dt.timedelta(days=31)
        await maintenance.purge(db, app.state.images, settings, now=later)
        assert await db.scalar(select(func.count()).select_from(SkySample)) == 0


async def test_unpairing_removes_sky_samples(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, status(10, 0.5, None, 10))
        # Wait until the hub stored it (a fixed sleep raced on a slow CI runner).
        for _ in range(100):
            async with app.state.sessionmaker() as db:
                if await db.scalar(select(func.count()).select_from(SkySample)):
                    break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("sky sample was not stored")
    await client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    async with app.state.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(SkySample)) == 0


def test_chart_gaps_and_scales() -> None:
    def sample(minute: int, clouds: float | None) -> SkySample:
        return SkySample(at=T0 + dt.timedelta(minutes=minute), cloud_cover=clouds,
                         sqm_mag=None, stars=None)  # fmt: skip

    result = charts([sample(0, 0.2), sample(10, 0.4), sample(20, None), sample(30, 0.8)])
    assert [c.title for c in result] == ["Bewölkung"]  # no SQM or stars: no empty charts
    svg = str(result[0].svg)
    assert svg.count("<polyline") == 1  # 0-10 min as a line
    assert svg.count("<circle") == 1  # after the gap a single point, not joined
    assert result[0].latest == "80"
    assert charts([]) == []


async def test_data_after_removal_is_dropped(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    """A status or event the hub reads after the camera was removed must not be stored."""
    from allskyhub_server.models import Device, EventRecord
    from tests.test_events import meteor

    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        async with app.state.sessionmaker() as db:  # removed while the socket is still open
            from sqlalchemy import update

            await db.execute(update(Device).where(Device.id == dev.device_id).values(owner_id=None))
            await db.commit()
        await send(ws, status(20, 0.3, None, 5))
        await send(ws, meteor())
        await asyncio.sleep(0.3)
    async with app.state.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(SkySample)) == 0
        assert await db.scalar(select(func.count()).select_from(EventRecord)) == 0
