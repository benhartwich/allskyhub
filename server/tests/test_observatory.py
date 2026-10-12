"""N.I.N.A. relayed by the camera (roadmap C15) and write-only settings (SPEC §6.9)."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Mode, Observatory, SettingSpec, Status
from allskyhub_server.devices import settings_form
from allskyhub_server.models import Device, PushToken, User
from allskyhub_server.push.notify import Notifier
from tests.fake_device import FakeDevice
from tests.test_public_page import _public, _save  # pyright: ignore[reportPrivateUsage]
from tests.test_push import FakeSender
from tests.ws_helpers import device_ws, paired_token, send

NOW = dt.datetime(2026, 10, 14, 21, tzinfo=dt.UTC)


def _status(safe: bool) -> Status:
    observatory = {"at": NOW.isoformat(), "connected": True, "target": "M31 Andromeda",
                   "sequence": {"name": "LRGB", "progress": 0.42},
                   "last_image": {"at": NOW.isoformat(), "filter": "L", "exposure_s": 120,
                                  "hfr": 2.31, "stars": 812},
                   "safe": safe, "something_new": 1}  # fmt: skip
    parsed = Observatory.model_validate(observatory)
    return Status(mode=Mode.NIGHT, exposure_us=1, gain=1, mean=0.1, uptime_s=1,
                  time_trusted=True, observatory=parsed)  # fmt: skip


async def test_observatory_on_pages(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == device_id)
            .values(last_status=_status(True).model_dump(mode="json"))
        )
        await db.commit()
    page = (await client.get(f"/cameras/{device_id}")).text
    for text in ("Teleskop", "M31 Andromeda", "LRGB · 42 %", "HFR 2,31", "812 Sterne", "sicher"):
        assert text in page, text
    assert "M31 Andromeda" not in (await new_client.get(f"/sky/{slug}")).text  # opt-in
    await _save(client, device_id, section_observatory="on")
    assert "M31 Andromeda" in (await new_client.get(f"/sky/{slug}")).text


async def test_push_when_the_sky_turns_unsafe(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    sender = FakeSender()
    app.state.notifier = Notifier(app.state.sessionmaker, sender)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        await db.execute(update(User).where(User.id == device.owner_id).values(notify_offline=True))
        db.add(PushToken(user_id=device.owner_id, token=f"t-{dev.device_id}", platform="android"))
        await db.commit()
    async with device_ws(live_app, token) as ws:
        await send(ws, _status(True))
        await send(ws, _status(False))
        await send(ws, _status(False))
        for _ in range(100):
            if sender.sent:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
    assert [m.title for _, m in sender.sent] == ["Garten: Himmel unsicher"]


def test_write_only_settings() -> None:
    schema = [
        SettingSpec.model_validate({"key": "nina_url", "type": "string", "label": "N.I.N.A.",
                                    "group": "Sternwarte"}),
        SettingSpec.model_validate({"key": "nina_api_key", "type": "string",
                                    "label": "API-Key", "group": "Sternwarte",
                                    "nullable": True, "write_only": True}),
    ]  # fmt: skip
    current: dict[str, Any] = {"nina_url": "http://192.168.1.30:1888", "nina_api_key_set": True}
    group = settings_form.groups(schema, current)[0]
    key = group.fields[1]
    assert key.is_set
    assert key.value == ""
    keep, error = settings_form.changes(schema, current, {"nina_url": current["nina_url"],
                                                          "nina_api_key": ""})  # fmt: skip
    assert (keep, error) == ({}, None)
    new, _ = settings_form.changes(schema, current, {"nina_api_key": "s3cret"})
    assert new == {"nina_api_key": "s3cret"}
    cleared, _ = settings_form.changes(schema, current, {"nina_api_key__clear": "1"})
    assert cleared == {"nina_api_key": None}
