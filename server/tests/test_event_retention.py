"""Per-account event retention (30 / 90 / 365 days), chosen by each user."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server import maintenance
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import EventRecord, User
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, csrf_from, owner_email
from tests.test_events import METEOR, NIGHT, expect_request, meteor
from tests.ws_helpers import JPEG, device_ws, paired_token, send


async def _set_web(client: httpx.AsyncClient, days: str) -> httpx.Response:
    page = await client.get("/account")
    return await client.post(
        "/account/events", data={"keep_days": days, "csrf_token": csrf_from(page.text)}
    )


async def test_setting_in_web_and_app(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    page = (await client.get("/account")).text
    assert "Ereignisse aufbewahren" in page
    assert 'value="30" checked' in page
    assert (await _set_web(client, "365")).status_code == 200
    assert 'value="365" checked' in (await client.get("/account")).text
    assert (await _set_web(client, "1000")).status_code == 400

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    got = (await client.get("/api/v1/account/settings", headers=auth)).json()
    assert got["event_keep_days"] == 365
    ok = await client.put("/api/v1/account/settings", json={"event_keep_days": 90}, headers=auth)
    assert ok.json()["event_keep_days"] == 90
    bad = await client.put("/api/v1/account/settings", json={"event_keep_days": 7}, headers=auth)
    assert bad.status_code == 400


async def _keep(app: FastAPI, device_id: str, days: int) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    email = await owner_email(app, device_id)
    async with maker() as db:
        await db.execute(update(User).where(User.email == email).values(event_keep_days=days))
        await db.commit()


async def test_longer_retention_fetches_and_keeps_the_full_picture(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, live_app: str
) -> None:
    keeper, default = FakeDevice(), FakeDevice()
    keeper_token = await paired_token(client, keeper)
    await _keep(app, keeper.device_id, 365)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as other:
        default_token = await paired_token(other, default)

    store: ImageStore = app.state.images
    for dev, token, expected in (
        (keeper, keeper_token, {FrameVariant.THUMB, FrameVariant.FULL}),
        (default, default_token, {FrameVariant.THUMB}),
    ):
        async with device_ws(live_app, token) as ws:
            await send(ws, meteor())
            asked = {(await expect_request(ws)).variant for _ in expected}
            assert asked == expected
        for variant in asked:  # as if the device had uploaded them
            path = store.event_path(dev.device_id, NIGHT, METEOR, variant)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(JPEG)
        async with app.state.sessionmaker() as db:
            await db.execute(
                update(EventRecord)
                .where(EventRecord.device_id == dev.device_id)
                .values(has_thumb=True, has_full=FrameVariant.FULL in asked)
            )
            await db.commit()

    now = dt.datetime.now(dt.UTC)
    async with app.state.sessionmaker() as db:
        await maintenance.purge(db, store, settings, now=now + dt.timedelta(days=31))
        remaining = await db.scalar(select(func.count()).select_from(EventRecord))
    assert remaining == 1  # only the one kept for a year
    assert store.event_path(keeper.device_id, NIGHT, METEOR, FrameVariant.FULL).exists()
    assert not (store.root / default.device_id / NIGHT / "events").exists()

    async with app.state.sessionmaker() as db:
        await maintenance.purge(db, store, settings, now=now + dt.timedelta(days=366))
        assert await db.scalar(select(func.count()).select_from(EventRecord)) == 0
    assert not store.event_path(keeper.device_id, NIGHT, METEOR, FrameVariant.FULL).exists()
