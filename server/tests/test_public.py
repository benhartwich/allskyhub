"""Opt-in public sky page (roadmap #9): off by default, shows only what is meant to be public."""

from __future__ import annotations

import datetime as dt
import re

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, Product
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, claim, home_csrf, make_account

JPEG = b"\xff\xd8\xff\xe0" + b"\x02" * 40


async def _camera(app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient) -> str:
    await make_account(client)
    dev = FakeDevice()
    reg = await dev.register(new_client)
    assert reg.pairing_code
    await claim(client, reg.pairing_code, "Sternwarte Nord")
    store: ImageStore = app.state.images
    store.save_frame(dev.device_id, "20261007", "a.jpg", FrameVariant.FULL, JPEG)
    store.product_path(dev.device_id, "20261007", "keogram.jpg", FrameVariant.THUMB).parent.mkdir(
        parents=True, exist_ok=True
    )
    store.product_path(dev.device_id, "20261007", "keogram.jpg", FrameVariant.THUMB).write_bytes(
        JPEG
    )
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(
                latest_image_at=dt.datetime.now(dt.UTC),
                last_frame={"mode": "night", "sun_elevation": -30.2},
                last_status={"cpu_temp_c": 55, "disk_free_pct": 40},
            )
        )
        db.add(
            Product(device_id=dev.device_id, night_id="20261007", kind="keogram",
                    name="keogram.jpg", content_type="image/jpeg", size=len(JPEG),
                    thumbnail=True, has_thumb=True)
        )  # fmt: skip
        await db.commit()
    return dev.device_id


async def _toggle(client: httpx.AsyncClient, device_id: str, enabled: bool) -> None:
    r = await client.post(
        f"/cameras/{device_id}/public",
        data={"enabled": "true" if enabled else "false", "csrf_token": await home_csrf(client)},
    )
    assert r.status_code == 303


async def test_public_page_is_opt_in_and_shows_only_public_things(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id = await _camera(app, client, new_client)
    page = (await client.get(f"/cameras/{device_id}")).text
    assert "Öffentliche Seite einschalten" in page

    await _toggle(client, device_id, True)
    page = (await client.get(f"/cameras/{device_id}")).text
    match = re.search(r'href="/sky/([A-Za-z0-9_-]+)"', page)
    assert match
    slug = match[1]
    assert slug != device_id

    # Anyone, without login.
    anon = await new_client.get(f"/sky/{slug}")
    assert anon.status_code == 200
    assert "Sternwarte Nord" in anon.text
    assert "Sonne -30.2°" in anon.text
    assert device_id not in anon.text  # no device id, no status internals
    assert "cpu" not in anon.text.lower()
    img = await new_client.get(f"/sky/{slug}/latest.jpg")
    assert img.content == JPEG
    assert img.headers["cache-control"] == "public, max-age=60"
    keogram = await new_client.get(f"/sky/{slug}/products/20261007/keogram.jpg")
    assert keogram.content == JPEG
    assert (await new_client.get(f"/sky/{slug}/products/20261007/timelapse.mp4")).status_code == 404
    assert (await new_client.get(f"/sky/{slug}/products/x/keogram.jpg")).status_code == 404
    # Public visitors do not switch on live view.
    assert not app.state.connections.is_live(device_id)

    # Off: the link is dead at once; on again: a new link.
    await _toggle(client, device_id, False)
    assert (await new_client.get(f"/sky/{slug}")).status_code == 404
    assert (await new_client.get(f"/sky/{slug}/latest.jpg")).status_code == 404
    await _toggle(client, device_id, True)
    page = (await client.get(f"/cameras/{device_id}")).text
    new_slug = re.search(r'href="/sky/([A-Za-z0-9_-]+)"', page)
    assert new_slug
    assert new_slug[1] != slug

    # Removing the camera ends the public page too.
    await client.post(f"/cameras/{device_id}/remove", data={"csrf_token": await home_csrf(client)})
    assert (await new_client.get(f"/sky/{new_slug[1]}")).status_code == 404


async def test_app_api_switches_the_public_page(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id = await _camera(app, client, new_client)
    from tests.helpers import owner_email

    r = await new_client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    cam = (await new_client.get(f"/api/v1/cameras/{device_id}", headers=auth)).json()
    assert cam["public_url"] is None
    on = await new_client.put(
        f"/api/v1/cameras/{device_id}/public", json={"enabled": True}, headers=auth
    )
    url = on.json()["public_url"]
    assert url.startswith("http://testserver/sky/")
    assert (await new_client.get(url.removeprefix("http://testserver"))).status_code == 200
    off = await new_client.put(
        f"/api/v1/cameras/{device_id}/public", json={"enabled": False}, headers=auth
    )
    assert off.json()["public_url"] is None


async def test_unknown_slug_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/sky/nope")).status_code == 404
    assert (await client.get("/sky/" + "x" * 40)).status_code == 404
