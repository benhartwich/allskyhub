"""Settings form from the camera's schema (SPEC §6.9)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Ack, Command, CommandName, Envelope, Hello, parse_envelope
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, csrf_from, owner_email
from tests.ws_helpers import TS, device_ws, online, paired_token, send

SCHEMA: list[dict[str, Any]] = [
    {"key": "location", "type": "location", "label": "Standort", "group": "Standort"},
    {"key": "max_gain", "type": "int", "label": "Höchster Gain", "group": "Belichtung",
     "min": 0, "max": 600, "help": "Mehr Gain rauscht stärker."},
    {"key": "flip", "type": "choice", "label": {"de": "Spiegeln", "en": "Flip"},
     "group": "Bild", "choices": [{"value": "none", "label": "Nein"},
                                  {"value": "h", "label": "Horizontal"}]},
    {"key": "overlay", "type": "bool", "label": "Overlay", "group": "Bild"},
    {"key": "title", "type": "string", "label": "Bildtitel", "group": "Bild",
     "max_length": 20, "nullable": True},
]  # fmt: skip
CURRENT = {"latitude": 50.0, "longitude": 10.4, "max_gain": 300, "flip": "none",
           "overlay": True, "title": "Garten"}  # fmt: skip


async def _device(app: FastAPI, device_id: str) -> Device:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        return device


async def test_schema_from_hello(app: FastAPI, client: httpx.AsyncClient, live_app: str) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        hello = Hello.model_validate({"device_id": dev.device_id, "profile": dev.profile,
                                      "agent_version": "2", "settings_schema": SCHEMA})  # fmt: skip
        await send(ws, hello)
        for _ in range(100):
            if (await _device(app, dev.device_id)).settings_schema:
                break
            await asyncio.sleep(0.05)
        stored = (await _device(app, dev.device_id)).settings_schema
        assert stored is not None
        assert [s["key"] for s in stored] == ["location", "max_gain", "flip", "overlay", "title"]
        # An inconsistent schema (a key twice) does not replace the good one.
        broken = Hello.model_validate({"device_id": dev.device_id, "profile": dev.profile,
                                       "agent_version": "2",
                                       "settings_schema": [SCHEMA[1], SCHEMA[1]]})  # fmt: skip
        await send(ws, broken)
        await asyncio.sleep(0.3)
        assert (await _device(app, dev.device_id)).settings_schema == stored


async def test_form_from_schema(app: FastAPI, client: httpx.AsyncClient, live_app: str) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(settings_schema=SCHEMA, last_status={"settings": CURRENT})
        )
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    for text in ("<summary>Belichtung</summary>", "Höchster Gain", "Erlaubt: 0 bis 600",
                 "Mehr Gain rauscht stärker.", 'value="h">Horizontal', "Spiegeln",
                 'name="overlay" value="true" checked', 'value="Garten"'):  # fmt: skip
        assert text in page, text
    url = f"/cameras/{dev.device_id}/settings"
    too_high = await client.post(url, data={"max_gain": "700", "csrf_token": csrf_from(page)})
    assert too_high.status_code == 400
    assert "Ungültige Angabe: Höchster Gain." in too_high.text
    assert 'value="700"' in too_high.text  # the input is kept

    async with device_ws(live_app, token) as ws:
        await online(app, dev.device_id)
        form = {"latitude": "50,0", "longitude": "10,4", "max_gain": "450", "flip": "h",
                "overlay__present": "1", "title": "", "csrf_token": csrf_from(page)}  # fmt: skip
        post = asyncio.create_task(client.post(url, data=form))
        env = parse_envelope(await asyncio.wait_for(ws.recv(), 5))
        assert isinstance(env.body, Command)
        assert env.body.name is CommandName.SET_SETTINGS
        # Only changes; the unticked box is false; the emptied nullable title is null.
        assert env.body.args == {"max_gain": 450, "flip": "h", "overlay": False, "title": None}
        await ws.send(Envelope.wrap(Ack(ref=env.id), ts=TS).model_dump_json())
        r = await post
        assert r.status_code == 200
        assert "Gespeichert" in r.text


async def test_app_api_checks_against_the_schema(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    r = await new_client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    cam = (await new_client.get(f"/api/v1/cameras/{dev.device_id}", headers=auth)).json()
    assert [s["key"] for s in cam["settings_schema"]][:2] == ["location", "timezone"]  # fallback

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device).where(Device.id == dev.device_id).values(settings_schema=SCHEMA)
        )
        await db.commit()
    url = f"/api/v1/cameras/{dev.device_id}/settings"
    bad = await new_client.put(url, json={"max_gain": 900}, headers=auth)
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Ungültige Angabe: Höchster Gain."
    async with device_ws(live_app, token) as ws:
        await online(app, dev.device_id)
        put = asyncio.create_task(new_client.put(url, json={"title": None}, headers=auth))
        await answer_any(ws, {"title": None})
        assert (await put).status_code == 200


async def answer_any(ws: Any, expected: dict[str, Any]) -> None:
    env = parse_envelope(await asyncio.wait_for(ws.recv(), 5))
    assert isinstance(env.body, Command)
    assert env.body.args == expected
    await ws.send(Envelope.wrap(Ack(ref=env.id), ts=TS).model_dump_json())


async def test_nothing_to_save_is_a_notice(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    """The values the camera already has (e.g. a page older than its last status)."""
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(settings_schema=SCHEMA, last_status={"settings": CURRENT})
        )
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    form = {"overlay__present": "1", "overlay": "true", "csrf_token": csrf_from(page)}
    r = await client.post(f"/cameras/{dev.device_id}/settings", data=form)
    assert r.status_code == 200
    assert "Nichts zu speichern" in r.text
