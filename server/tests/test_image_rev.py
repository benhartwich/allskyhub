"""Replaceable event pictures (SPEC §6.4 data.image_rev), e.g. a growing aurora episode."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from fastapi import FastAPI

from allskyhub_protocol import Event, EventKind, FrameVariant, event_id
from allskyhub_server.devices.event_text import event_rows
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import EventRecord
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, owner_email
from tests.test_events import expect_request
from tests.ws_helpers import JPEG, device_ws, paired_token, send

NIGHT = "20261008"
START = dt.datetime(2026, 10, 8, 22, 0, tzinfo=dt.UTC)
AURORA = event_id(EventKind.AURORA, START)


def aurora(rev: int, minutes: int, ongoing: bool = True) -> Event:
    return Event(
        id=AURORA, night_id=NIGHT, kind=EventKind.AURORA, start=START,
        end=START + dt.timedelta(minutes=minutes), confidence=0.7, has_image=True,
        data={"peak_index": 10 * rev, "green": 1.8, "frames": 2 + minutes,
              "direction_deg": 40.0, "ongoing": ongoing, "image_rev": rev},
    )  # fmt: skip


async def put_thumb(host: str, token: str, body: bytes) -> int:
    async with httpx.AsyncClient(base_url=f"http://{host}") as dc:
        r = await dc.put(
            f"/device/v1/events/{NIGHT}/{AURORA}",
            params={"variant": "thumb"},
            content=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code


def test_aurora_rows() -> None:
    row = EventRecord(
        kind="aurora", confidence=0.7, start=START, end=START + dt.timedelta(minutes=12),
        data=dict(aurora(2, 12).data),
    )  # fmt: skip
    rows = dict(event_rows(row))
    assert rows["Stärke"] == "20 %"
    assert rows["Bildrichtung"] == "40°"
    assert rows["Status"] == "läuft noch"
    assert "image_rev" not in rows
    assert not any("Nord" in v or "Ost" in v for v in rows.values())


async def test_replaced_picture_is_fetched_again(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    store: ImageStore = app.state.images
    thumb = store.event_path(dev.device_id, NIGHT, AURORA, FrameVariant.THUMB)
    async with device_ws(live_app, token) as ws:
        await send(ws, aurora(1, 0))
        assert (await expect_request(ws)).variant is FrameVariant.THUMB
        assert await put_thumb(live_app, token, JPEG) == 204
        assert thumb.read_bytes() == JPEG

        await send(ws, aurora(1, 5))  # grows, same picture: nothing to fetch
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.3)

        await send(ws, aurora(2, 10))  # a better frame replaced the picture
        assert (await expect_request(ws)).variant is FrameVariant.THUMB
        assert not thumb.exists()  # the old one is never served under the new revision
        newer = JPEG + b"\x01"
        assert await put_thumb(live_app, token, newer) == 204
        assert thumb.read_bytes() == newer

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    listed = (await client.get(f"/api/v1/cameras/{dev.device_id}/events", headers=auth)).json()
    assert listed[0]["image_rev"] == 2
    assert listed[0]["data"]["frames"] == 12
    page = (await client.get(f"/cameras/{dev.device_id}/events/{NIGHT}/{AURORA}")).text
    assert "v=2" in page
    assert "läuft noch" in page


def test_nlc_rows() -> None:
    row = EventRecord(
        kind="nlc", confidence=0.6, start=START, end=START + dt.timedelta(minutes=30),
        data={"peak_index": 25, "blue": 142.0, "frames": 9, "direction_deg": 300.0,
              "ongoing": False, "image_rev": 2, "azimuth_deg": 341.0, "altitude_deg": 18.0},
    )  # fmt: skip
    rows = dict(event_rows(row))
    assert rows["Blauanteil"] == "142"
    assert rows["Stärke"] == "25 %"
    assert rows["Himmelsrichtung"] == "341° (N)"
    assert "Status" not in rows  # closed episode
