# pyright: reportPrivateUsage=false
"""Fixes from the security review (2026-10-13): what the hub must not give away."""

from __future__ import annotations

import time

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.api import device as device_api
from allskyhub_server.devices import public
from allskyhub_server.devices import settings as camera_settings
from allskyhub_server.devices.connections import ConnectionRegistry
from allskyhub_server.models import Device
from tests.helpers import home_csrf
from tests.test_public_page import (
    _add_archive,
    _public,
    _save,
)


async def _device(app: FastAPI, device_id: str) -> Device:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
    assert device is not None
    return device


async def test_unpairing_forgets_the_owners_choices(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """A camera given away must not post to the old owner's Discord, nor carry their
    public texts or map and start-page choices to the next owner."""
    device_id, _ = await _public(app, client, new_client)
    await _save(client, device_id, title="Sternwarte", show_coordinates="on", on_home="on")
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        device.discord = {"webhook": "https://discord.com/api/webhooks/1/x"}
        device.network = True
        await db.commit()
    r = await client.post(
        f"/cameras/{device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    assert r.status_code == 303
    device = await _device(app, device_id)
    assert (device.discord, device.public_profile, device.public_on_home) == (None, None, False)
    assert device.network is False


async def test_switched_off_sections_stay_off(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """Not only hidden in the page: the image and product URLs answer 404 too."""
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    store = app.state.images
    from allskyhub_protocol import FrameVariant

    latest = store.path(device_id, FrameVariant.FULL)
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    assert (await new_client.get(f"/sky/{slug}/latest.jpg")).status_code == 200

    await _save(client, device_id, title="Sternwarte")  # every section unticked
    for url in ("latest.jpg", "now", "widget", "products/20261012/keogram.jpg?full=1"):
        assert (await new_client.get(f"/sky/{slug}/{url}")).status_code == 404, url
    page = (await new_client.get(f"/sky/{slug}")).text
    assert "og:image" not in page


async def test_network_settings_are_for_the_owner_only() -> None:
    outcome = await camera_settings.apply(
        ConnectionRegistry(), "dev", {"adsb_url": "http://192.168.1.1:22/"},
        {"adsb_url": "ADS-B-Empfänger"}, owner=False,
    )  # fmt: skip
    assert (outcome.ok, outcome.status) == (False, 403)
    assert outcome.message == "Nur der Besitzer kann das ändern: ADS-B-Empfänger."
    offline = await camera_settings.apply(
        ConnectionRegistry(), "dev", {"night_delay_s": 5}, owner=False
    )
    assert offline.status == 409  # other settings go on as before (here: camera offline)


def test_public_tonight_uses_a_coarse_place() -> None:
    status = {"settings": {"latitude": 50.036, "longitude": 10.39, "timezone": "Europe/Vienna"}}
    device = Device(id="d", last_status=status, public_profile=None)
    coarse = public.coarse_status(device)
    assert coarse is not None
    assert (coarse["settings"]["latitude"], coarse["settings"]["longitude"]) == (50.0, 10.5)
    assert status["settings"]["latitude"] == 50.036  # untouched
    shown = Device(id="d", last_status=status, public_profile={"show_coordinates": True})
    assert public.coarse_status(shown) is status


async def test_device_messages_are_bounded() -> None:
    data = {f"k{i}": "x" * 1000 for i in range(100)}
    bounded = device_api._bounded(data)
    assert len(bounded) == device_api.DATA_KEYS
    assert all(len(v) == device_api.DATA_TEXT for v in bounded.values())

    bucket = device_api._Bucket()
    start = time.monotonic()
    for _ in range(device_api.BURST):
        await bucket.take()
    assert time.monotonic() - start < 0.5  # a backlog burst goes through at once
    for _ in range(10):
        await bucket.take()
    assert time.monotonic() - start >= 9 / device_api.PER_SECOND  # then it is slowed
