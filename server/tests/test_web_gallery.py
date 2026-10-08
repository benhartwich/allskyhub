"""Gallery in the web UI (roadmap #13)."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import CommandName, FrameVariant, UploadProductArgs
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Frame, Product
from allskyhub_server.web.templating import night_title
from tests.fake_device import FakeDevice
from tests.helpers import make_account
from tests.ws_helpers import JPEG, device_ws, next_command, paired_token

NIGHT = "20261006"


async def _seed(app: FastAPI, device_id: str) -> None:
    store: ImageStore = app.state.images
    store.save_frame(device_id, NIGHT, "a.jpg", FrameVariant.THUMB, JPEG)
    store.save_frame(device_id, NIGHT, "a.jpg", FrameVariant.FULL, JPEG)
    keogram = store.product_path(device_id, NIGHT, "keogram.jpg", FrameVariant.FULL)
    keogram.parent.mkdir(parents=True, exist_ok=True)
    keogram.write_bytes(JPEG)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(
            Frame(device_id=device_id, night_id=NIGHT, name="a.jpg",
                  captured_at=dt.datetime(2026, 10, 6, 20, 15, tzinfo=dt.UTC), mode="night",
                  exposure_us=30_000_000, gain=100, mean=0.2, sun_elevation=-20,
                  has_full=True, has_thumb=True)
        )  # fmt: skip
        db.add_all(
            [
                Product(device_id=device_id, night_id=NIGHT, kind="keogram", name="keogram.jpg",
                        content_type="image/jpeg", size=300_000, thumbnail=True, has_full=True),
                Product(device_id=device_id, night_id=NIGHT, kind="timelapse",
                        name="timelapse.mp4", content_type="video/mp4", size=250 * 1024 * 1024,
                        duration_s=95.4, thumbnail=True),
            ]
        )  # fmt: skip
        await db.commit()


def test_night_title() -> None:
    assert night_title("20261006") == "Di 06.10. → Mi 07.10."
    assert night_title("20261231") == "Do 31.12. → Fr 01.01."


async def test_gallery_night_and_products(
    app: FastAPI, client: httpx.AsyncClient, live_app: str, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    await _seed(app, dev.device_id)
    base = f"/cameras/{dev.device_id}"

    assert f'href="{base}/gallery"' in (await client.get(base)).text
    gallery = await client.get(f"{base}/gallery")
    assert "Di 06.10. → Mi 07.10." in gallery.text
    assert "1 Bild" in gallery.text
    assert "1 Bilder" not in gallery.text
    assert "2 Produkte" in gallery.text

    night = await client.get(f"{base}/nights/{NIGHT}")
    assert f"{base}/frames/{NIGHT}/a.jpg/thumb.jpg" in night.text
    assert "Zeitraffer · 1:35 · 250,0 MB" in night.text
    assert (await client.get(f"{base}/frames/{NIGHT}/a.jpg/full.jpg")).content == JPEG

    keogram = await client.get(f"{base}/products/{NIGHT}/keogram.jpg")
    assert f'src="{base}/products/{NIGHT}/keogram.jpg/file"' in keogram.text
    assert (await client.get(f"{base}/products/{NIGHT}/keogram.jpg/file")).content == JPEG

    # The timelapse is not on the hub: offline camera → explained, nothing requested.
    offline = await client.get(f"{base}/products/{NIGHT}/timelapse.mp4")
    assert "Die Kamera ist offline" in offline.text

    # Connected: the page asks the camera once and polls.
    async with device_ws(live_app, token) as ws:
        await asyncio.sleep(0.1)
        page = await client.get(f"{base}/products/{NIGHT}/timelapse.mp4")
        assert "Wird von der Kamera geholt" in page.text
        cmd = await next_command(ws)
        assert cmd.name is CommandName.UPLOAD_PRODUCT
        assert UploadProductArgs.model_validate(cmd.args).name == "timelapse.mp4"
        state = await client.get(f"{base}/products/{NIGHT}/timelapse.mp4/state")
        assert "Wird von der Kamera geholt" in state.text
        with pytest.raises(TimeoutError):  # no second request while pending
            await asyncio.wait_for(ws.recv(), 0.3)

    # Other accounts see nothing.
    await make_account(new_client)
    for path in (
        f"{base}/gallery",
        f"{base}/nights/{NIGHT}",
        f"{base}/products/{NIGHT}/keogram.jpg",
    ):
        assert (await new_client.get(path)).status_code == 404
