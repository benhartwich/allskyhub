"""Night products: announcement, uploads, on-demand timelapse, app API, retention (SPEC §6.3,
§6.5, §6.6)."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection

from allskyhub_protocol import (
    CommandName,
    FrameVariant,
    ProductFile,
    ProductKind,
    Products,
    UploadProductArgs,
)
from allskyhub_server import maintenance
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, home_csrf, owner_email
from tests.ws_helpers import device_ws, next_command, paired_token, send

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64
NIGHT = "20261006"


def products(timelapse_size: int = len(MP4)) -> Products:
    return Products(
        night_id=NIGHT,
        products=[
            ProductFile(kind=ProductKind.KEOGRAM, name="keogram.jpg", content_type="image/jpeg",
                        size=len(JPEG), thumbnail=True),
            ProductFile(kind=ProductKind.STARTRAILS, name="startrails.jpg",
                        content_type="image/jpeg", size=len(JPEG), thumbnail=True),
            ProductFile(kind=ProductKind.TIMELAPSE, name="timelapse.mp4", content_type="video/mp4",
                        size=timelapse_size, thumbnail=True, duration_s=42.5),
        ],
    )  # fmt: skip


async def requested(ws: ClientConnection, count: int) -> set[tuple[str, FrameVariant]]:
    got: set[tuple[str, FrameVariant]] = set()
    for _ in range(count):
        cmd = await next_command(ws)
        assert cmd.name is CommandName.UPLOAD_PRODUCT
        args = UploadProductArgs.model_validate(cmd.args)
        assert args.night_id == NIGHT
        got.add((args.name, args.variant))
    return got


async def put(
    host: str, token: str, name: str, variant: FrameVariant, body: bytes, content_type: str
) -> int:
    async with httpx.AsyncClient(base_url=f"http://{host}") as dc:
        r = await dc.put(
            f"/device/v1/products/{NIGHT}/{name}",
            params={"variant": variant.value},
            content=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        )
        return r.status_code


async def test_products_flow(
    app: FastAPI, client: httpx.AsyncClient, live_app: str, settings: Settings
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    api = f"/api/v1/cameras/{dev.device_id}"

    async with device_ws(live_app, token) as ws:
        await send(ws, products())
        # All thumbnails plus keogram and startrails in full; the timelapse waits.
        assert await requested(ws, 5) == {
            ("keogram.jpg", FrameVariant.THUMB),
            ("startrails.jpg", FrameVariant.THUMB),
            ("timelapse.mp4", FrameVariant.THUMB),
            ("keogram.jpg", FrameVariant.FULL),
            ("startrails.jpg", FrameVariant.FULL),
        }
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.3)

        # Wrong type or body: rejected, but the request stays (retry works).
        full = FrameVariant.FULL
        assert await put(live_app, token, "keogram.jpg", full, MP4, "video/mp4") == 400
        assert await put(live_app, token, "keogram.jpg", full, MP4, "image/jpeg") == 400
        assert await put(live_app, token, "keogram.jpg", full, JPEG, "image/jpeg") == 204
        assert await put(live_app, token, "keogram.jpg", full, JPEG, "image/jpeg") == 404
        assert await put(live_app, token, "timelapse.mp4", full, MP4, "video/mp4") == 404
        assert await put(live_app, token, "other.jpg", full, JPEG, "image/jpeg") == 404
        for name in ("keogram.jpg", "startrails.jpg", "timelapse.mp4"):
            status = await put(live_app, token, name, FrameVariant.THUMB, JPEG, "image/jpeg")
            assert status == 204

        listing = (await client.get(f"{api}/nights/{NIGHT}/products", headers=auth)).json()
        by_name = {p["name"]: p for p in listing}
        assert by_name["keogram.jpg"]["has_full"] is True
        assert by_name["startrails.jpg"]["has_full"] is False  # not uploaded yet
        assert by_name["timelapse.mp4"]["has_thumb"] is True
        assert by_name["timelapse.mp4"]["duration_s"] == 42.5
        nights = (await client.get(f"{api}/nights", headers=auth)).json()
        assert nights == [
            {
                "night_id": NIGHT,
                "frames": 0,
                "first": None,
                "last": None,
                "products": 3,
                "events": 0,
                "sky": 0,
            }
        ]

        # Opening the timelapse asks the camera for it.
        video = f"{api}/products/{NIGHT}/timelapse.mp4"
        first = await client.get(video, headers=auth)
        assert first.status_code == 202
        assert await requested(ws, 1) == {("timelapse.mp4", FrameVariant.FULL)}
        assert (await client.get(video, headers=auth)).status_code == 202  # no second command
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.3)
        listing = (await client.get(f"{api}/nights/{NIGHT}/products", headers=auth)).json()
        assert {p["name"]: p["pending"] for p in listing}["timelapse.mp4"] is True
        assert await put(live_app, token, "timelapse.mp4", full, MP4, "video/mp4") == 204
        ok = await client.get(video, headers=auth)
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "video/mp4"
        part = await client.get(video, headers={**auth, "Range": "bytes=4-11"})
        assert part.status_code == 206
        assert part.content == b"ftypisom"
        thumb = await client.get(video, params={"variant": "thumb"}, headers=auth)
        assert thumb.content == JPEG

        # A rebuilt timelapse (other size) is fetched again.
        await send(ws, products(timelapse_size=len(MP4) + 1))
        assert ("timelapse.mp4", FrameVariant.THUMB) in await requested(ws, 2)

    # Retention: the video after 7 days, everything after 30.
    store: ImageStore = app.state.images
    now = dt.datetime.now(dt.UTC)
    keogram = store.product_path(dev.device_id, NIGHT, "keogram.jpg", FrameVariant.FULL)
    async with app.state.sessionmaker() as db:
        await maintenance.purge(db, store, settings, now=now + dt.timedelta(days=31))
    assert not keogram.exists()
    listing = (await client.get(f"{api}/nights/{NIGHT}/products", headers=auth)).json()
    assert listing == []


async def test_offline_camera_gives_404_for_missing_video(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, products())
        await requested(ws, 5)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await asyncio.sleep(0.1)  # the WebSocket is gone
    video = f"/api/v1/cameras/{dev.device_id}/products/{NIGHT}/timelapse.mp4"
    assert (await client.get(video, headers=auth)).status_code == 404

    await client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    store: ImageStore = app.state.images
    assert not (store.root / dev.device_id).exists()


async def test_public_page_gets_last_nights_timelapse(
    app: FastAPI, client: httpx.AsyncClient, live_app: str, new_client: httpx.AsyncClient
) -> None:
    """Roadmap #16: like Allsky's website, a public page with its archive shows the night's
    timelapse; the hub fetches it for recent nights only (no flood after an import)."""
    dev = FakeDevice()
    token = await paired_token(client, dev)
    r = await client.post(
        f"/cameras/{dev.device_id}/public",
        data={"enabled": "true", "csrf_token": await home_csrf(client)},
    )
    assert r.status_code == 303
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    slug = page.split('href="/sky/', 1)[1].split('"', 1)[0]
    recent = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).strftime("%Y%m%d")
    announced = products().model_copy(update={"night_id": recent})
    async with device_ws(live_app, token) as ws:
        await send(ws, announced)
        got: set[tuple[str, FrameVariant]] = set()
        for _ in range(6):
            cmd = await next_command(ws)
            args = UploadProductArgs.model_validate(cmd.args)
            got.add((args.name, args.variant))
        assert ("timelapse.mp4", FrameVariant.FULL) in got
        async with httpx.AsyncClient(base_url=f"http://{live_app}") as dc:
            r = await dc.put(
                f"/device/v1/products/{recent}/timelapse.mp4",
                params={"variant": "full"},
                content=MP4,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
            )
            assert r.status_code == 204
        # An old night (an import): the timelapse waits for the owner, as before.
        await send(ws, products())
        old = await requested(ws, 5)
        assert ("timelapse.mp4", FrameVariant.FULL) not in old

    video = await new_client.get(f"/sky/{slug}/nights/{recent}/timelapse.mp4")
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/mp4"
    night_page = (await new_client.get(f"/sky/{slug}/nights/{recent}")).text
    assert f"/sky/{slug}/nights/{recent}/timelapse.mp4" in night_page
    assert (await new_client.get(f"/sky/{slug}/nights/{NIGHT}/timelapse.mp4")).status_code == 404


async def test_day_products(
    app: FastAPI, client: httpx.AsyncClient, live_app: str, settings: Settings
) -> None:
    """SPEC §5.2 day products: the day keogram comes in full at once, the day timelapse
    only on request and expires like the night's."""
    dev = FakeDevice()
    token = await paired_token(client, dev)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    api = f"/api/v1/cameras/{dev.device_id}"
    day = Products(
        night_id=NIGHT,
        products=[
            *products().products,
            ProductFile(kind=ProductKind.DAY_TIMELAPSE, name="day-timelapse.mp4",
                        content_type="video/mp4", size=len(MP4), thumbnail=True, duration_s=30),
            ProductFile(kind=ProductKind.DAY_KEOGRAM, name="day-keogram.jpg",
                        content_type="image/jpeg", size=len(JPEG), thumbnail=True),
        ],
    )  # fmt: skip
    async with device_ws(live_app, token) as ws:
        await send(ws, day)
        got = await requested(ws, 8)
        assert ("day-keogram.jpg", FrameVariant.FULL) in got
        assert ("day-timelapse.mp4", FrameVariant.THUMB) in got
        assert ("day-timelapse.mp4", FrameVariant.FULL) not in got
        video = f"{api}/products/{NIGHT}/day-timelapse.mp4"
        assert (await client.get(video, headers=auth)).status_code == 202
        assert await requested(ws, 1) == {("day-timelapse.mp4", FrameVariant.FULL)}
        full = FrameVariant.FULL
        assert await put(live_app, token, "day-timelapse.mp4", full, MP4, "video/mp4") == 204
        assert await put(live_app, token, "day-keogram.jpg", full, JPEG, "image/jpeg") == 204

    page = (await client.get(f"/cameras/{dev.device_id}/nights/{NIGHT}")).text
    titles = ["Keogramm", "Sternspuren", "Zeitraffer", "Keogramm am Tag", "Zeitraffer am Tag"]
    positions = [page.index(f"{t} ·") if t != "Zeitraffer" else page.index("Zeitraffer · ")
                 for t in titles]  # fmt: skip
    assert positions == sorted(positions)  # the night's products first

    store: ImageStore = app.state.images
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=8)
    async with app.state.sessionmaker() as db:
        await maintenance.purge(db, store, settings, now=later)
    path = store.product_path
    assert not path(dev.device_id, NIGHT, "day-timelapse.mp4", FrameVariant.FULL).exists()
    assert path(dev.device_id, NIGHT, "day-keogram.jpg", FrameVariant.FULL).exists()
