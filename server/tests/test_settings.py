"""Changing camera settings from the hub (SPEC §6.5 set_settings, roadmap #2)."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from websockets.asyncio.client import ClientConnection

from allskyhub_protocol import (
    Ack,
    Command,
    CommandName,
    Envelope,
    ErrorReply,
    SetSettingsArgs,
    parse_envelope,
)
from allskyhub_server.devices import settings as camera_settings
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, csrf_from, owner_email
from tests.ws_helpers import device_ws, paired_token

TS = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.UTC)


async def answer(ws: ClientConnection, reply: str = "ack", message: str = "") -> SetSettingsArgs:
    """Read the set_settings command and answer it like the agent."""
    env = parse_envelope(await asyncio.wait_for(ws.recv(), 5))
    assert isinstance(env.body, Command)
    assert env.body.name is CommandName.SET_SETTINGS
    args = SetSettingsArgs.model_validate(env.body.args)
    body = (
        Ack(ref=env.id)
        if reply == "ack"
        else ErrorReply(ref=env.id, code="invalid_args", message=message)
    )
    await ws.send(Envelope.wrap(body, ts=TS).model_dump_json())
    return args


def test_field_names_in_german() -> None:
    assert camera_settings.describe_fields("longitude") == "Länge"
    assert camera_settings.describe_fields("timezone, night_delay_s") == (
        "Zeitzone, Pause in der Nacht"
    )


async def test_app_api_settings(app: FastAPI, client: httpx.AsyncClient, live_app: str) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    url = f"/api/v1/cameras/{dev.device_id}/settings"

    offline = await client.put(url, json={"night_delay_s": 5}, headers=auth)
    assert offline.status_code == 409
    invalid = await client.put(url, json={"latitude": 48.1}, headers=auth)
    assert invalid.status_code == 400  # latitude without longitude, checked by the hub

    async with device_ws(live_app, token) as ws:
        await asyncio.sleep(0.1)
        put = asyncio.create_task(
            client.put(
                url,
                json={"latitude": 47.8, "longitude": 13.05, "timezone": "Europe/Vienna"},
                headers=auth,
            )
        )
        args = await answer(ws)
        assert (args.latitude, args.longitude, args.timezone) == (47.8, 13.05, "Europe/Vienna")
        assert args.camera is None  # only the given keys are sent
        r = await put
        assert r.status_code == 200
        assert r.json() == {"status": "applied"}

        put = asyncio.create_task(client.put(url, json={"camera": "rpi-hq"}, headers=auth))
        await answer(ws, "error", "camera")
        r = await put
        assert r.status_code == 400
        assert r.json()["detail"] == "Die Kamera hat abgelehnt: Kamera."

        empty = await client.put(url, json={}, headers=auth)
        assert empty.status_code == 400


async def test_web_settings_form(app: FastAPI, client: httpx.AsyncClient, live_app: str) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(
                last_status={
                    "settings": {"latitude": 48.14, "longitude": 14.39, "timezone": "Europe/Vienna",
                                 "camera": "zwo-asi678mc", "day_delay_s": 30.0,
                                 "night_delay_s": 0.0},
                }
            )
        )  # fmt: skip
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert 'value="48.14"' in page
    assert 'value="zwo-asi678mc" selected' in page
    assert "disabled" in page  # offline: cannot apply
    url = f"/cameras/{dev.device_id}/settings"

    bad = await client.post(
        url, data={"latitude": "48,1", "longitude": "", "csrf_token": csrf_from(page)}
    )
    assert bad.status_code == 400
    assert "beide oder keine" in bad.text

    async with device_ws(live_app, token) as ws:
        await asyncio.sleep(0.1)
        page = (await client.get(f"/cameras/{dev.device_id}")).text
        post = asyncio.create_task(
            client.post(
                url,
                data={"latitude": "47,8", "longitude": "13,05", "timezone": "Europe/Vienna",
                      "camera": "", "day_delay_s": "60", "night_delay_s": "",
                      "csrf_token": csrf_from(page)},
            )
        )  # fmt: skip
        args = await answer(ws)
        assert args.model_dump(exclude_none=True) == {
            "latitude": 47.8,
            "longitude": 13.05,
            "timezone": "Europe/Vienna",
            "day_delay_s": 60.0,
        }
        r = await post
        assert r.status_code == 200
        assert "Gespeichert" in r.text
