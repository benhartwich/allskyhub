"""The stream view for OBS (draft C, web/routes_obs.py)."""

from __future__ import annotations

import re

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.helpers import csrf_from, make_account
from tests.test_sky_overlay import AT, _status  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import paired_token


async def _camera(app: FastAPI, client: httpx.AsyncClient) -> str:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.name = "Gartensternwarte"
        device.last_status = {**_status(), "exposure_us": 30_000_000,
                              "sky": {"cloud_cover": 0.05}}  # fmt: skip
        device.last_frame = {"captured_at": AT.isoformat()}
        device.latest_image_at = AT
        await db.commit()
    return dev.device_id


async def _new_key(client: httpx.AsyncClient, device_id: str) -> tuple[str, str]:
    page = await client.get(f"/cameras/{device_id}/stream")
    assert page.status_code == 200
    r = await client.post(
        f"/cameras/{device_id}/stream",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=True,
    )
    assert r.status_code == 200
    found = re.search(r'data-base="http://testserver/obs/([A-Za-z0-9_-]+)"', r.text)
    assert found
    return found[1], r.text


async def test_key_view_and_revoke(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    assert "Adresse für Streams" in (await client.get(f"/cameras/{device_id}")).text
    token, page = await _new_key(client, device_id)
    assert "verrät den genauen Ort" in page
    again = await client.get(f"/cameras/{device_id}/stream")
    assert token not in again.text  # shown once
    assert "Aktive Adresse" in again.text

    async with httpx.AsyncClient(
        transport=client._transport,  # pyright: ignore[reportPrivateUsage]
        base_url=str(client.base_url),
    ) as obs:  # OBS is not signed in
        view = await obs.get(f"/obs/{token}", params={"ebenen": "sternbilder,namen,quatsch"})
        assert view.status_code == 200
        assert "frame-ancestors *" in view.headers["content-security-policy"]
        assert 'class="obs sky-on stars-on"' in view.text
        assert 'class="sky-overlay"' in view.text
        assert "Gartensternwarte" in view.text
        assert "30 s" in view.text
        assert "klar" in view.text
        assert "50.04" not in view.text  # the place is never written
        # Satellites only when picked; the default is the constellations alone.
        plain = await obs.get(f"/obs/{token}")
        assert 'class="obs sky-on"' in plain.text
        bare = await obs.get(f"/obs/{token}", params={"text": "aus", "rotlicht": "1"})
        assert 'class="obs-text"' not in bare.text
        assert "obs-red" in bare.text
        frame = await obs.get(f"/obs/{token}/rahmen")
        assert frame.status_code == 200
        assert 'class="obs-frame"' in frame.text
        assert "<html" not in frame.text
        assert (await obs.get(f"/obs/{token}/bild.jpg")).status_code == 404  # no file here

        # A new key replaces the old one; revoking ends it.
        newer, _ = await _new_key(client, device_id)
        assert (await obs.get(f"/obs/{token}")).status_code == 404
        assert (await obs.get(f"/obs/{newer}")).status_code == 200
        page2 = await client.get(f"/cameras/{device_id}/stream")
        await client.post(
            f"/cameras/{device_id}/stream/loeschen", data={"csrf_token": csrf_from(page2.text)}
        )
        assert (await obs.get(f"/obs/{newer}")).status_code == 404
        assert (await obs.get("/obs/nothing")).status_code == 404


async def test_only_the_owner(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id = await _camera(app, client)
    await make_account(new_client)
    assert (await new_client.get(f"/cameras/{device_id}/stream")).status_code == 404
