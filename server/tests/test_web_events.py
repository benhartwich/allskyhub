"""Detections in the web UI: camera page, list, night, detail with on-demand picture."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import CommandName, FrameVariant, UploadEventArgs
from allskyhub_server.devices.event_text import event_rows
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import EventRecord
from tests.fake_device import FakeDevice
from tests.helpers import make_account
from tests.ws_helpers import JPEG, device_ws, next_command, paired_token

NIGHT = "20261008"
EVENT = "meteor-20261008T214512Z"
START = dt.datetime(2026, 10, 8, 21, 45, 12, tzinfo=dt.UTC)


def record(device_id: str) -> EventRecord:
    return EventRecord(
        device_id=device_id, event_id=EVENT, night_id=NIGHT, kind="meteor", start=START,
        end=START + dt.timedelta(seconds=1.5), confidence=0.87, has_image=True, has_thumb=True,
        data={"length_px": 412, "peak": 0.93, "frames": 3, "direction_deg": 135.0,
              "shower": "Orioniden", "extra": 7},
    )  # fmt: skip


def test_event_rows_in_german() -> None:
    rows = dict(event_rows(record("x")))
    assert rows == {
        "Sicherheit": "87 %",
        "Dauer": "1,5 s",
        "Spurlänge": "412 px",
        "Helligkeit": "93 %",
        "Bilder": "3",
        "Richtung": "135° (diagonal, oben links – unten rechts)",
        "Meteorstrom": "Orioniden",
        "extra": "7",
    }


async def test_web_events(
    app: FastAPI, client: httpx.AsyncClient, live_app: str, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    store: ImageStore = app.state.images
    thumb = store.event_path(dev.device_id, NIGHT, EVENT, FrameVariant.THUMB)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(JPEG)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(record(dev.device_id))
        await db.commit()
    base = f"/cameras/{dev.device_id}"
    detail = f"{base}/events/{NIGHT}/{EVENT}"

    camera = (await client.get(base)).text
    assert "Letzte Ereignisse" in camera
    assert f'href="{detail}"' in camera
    assert f'href="{detail}"' in (await client.get(f"{base}/events")).text
    assert f'href="{detail}"' in (await client.get(f"{base}/nights/{NIGHT}")).text
    image = await client.get(f"{detail}/image", params={"variant": "thumb"})
    assert image.content == JPEG

    offline = (await client.get(detail)).text
    assert "Orioniden" in offline
    assert "Das Vollbild kommt, sobald die Kamera wieder verbunden ist" in offline

    async with device_ws(live_app, token) as ws:
        await asyncio.sleep(0.1)
        page = (await client.get(detail)).text
        assert "Vollbild wird von der Kamera geholt" in page
        cmd = await next_command(ws)
        assert cmd.name is CommandName.UPLOAD_EVENT
        assert UploadEventArgs.model_validate(cmd.args).variant is FrameVariant.FULL
        state = (await client.get(f"{detail}/state")).text
        assert "Vollbild wird von der Kamera geholt" in state
        with pytest.raises(TimeoutError):  # asked only once
            await asyncio.wait_for(ws.recv(), 0.3)

    await make_account(new_client)
    for path in (f"{base}/events", detail, f"{detail}/image"):
        assert (await new_client.get(path)).status_code == 404
