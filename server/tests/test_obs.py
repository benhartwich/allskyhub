"""The stream view for OBS (draft C, web/routes_obs.py)."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.app import log_path
from allskyhub_server.devices import pairing
from allskyhub_server.models import Device, ObsToken
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
    assert "verraten den genauen Ort" in page  # the box when making a key
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


async def test_place_layers_only_when_the_key_allows_them(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """The address can only leave layers out (allsky review of #463)."""
    device_id = await _camera(app, client)
    token, page = await _new_key(client, device_id)
    assert 'value="satelliten"' not in page  # not offered for this key
    async with httpx.AsyncClient(
        transport=client._transport,  # pyright: ignore[reportPrivateUsage]
        base_url=str(client.base_url),
    ) as obs:
        view = await obs.get(f"/obs/{token}", params={"ebenen": "sternbilder,satelliten,ballons"})
        assert 'class="obs sky-on"' in view.text
        # Allowed when the key is made: then the address may ask for them.
        settings = await client.get(f"/cameras/{device_id}/stream")
        r = await client.post(
            f"/cameras/{device_id}/stream",
            data={"csrf_token": csrf_from(settings.text), "place_layers": "true"},
            follow_redirects=True,
        )
        found = re.search(r'data-base="http://testserver/obs/([A-Za-z0-9_-]+)"', r.text)
        assert found
        assert 'value="satelliten"' in r.text
        view = await obs.get(f"/obs/{found[1]}", params={"ebenen": "satelliten"})
        assert 'class="obs sats-on"' in view.text


async def test_key_ends_with_the_pairing_and_twice_is_fine(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    device_id = await _camera(app, client)
    page = await client.get(f"/cameras/{device_id}/stream")
    for _ in range(2):  # a double click makes one key, no error
        r = await client.post(
            f"/cameras/{device_id}/stream", data={"csrf_token": csrf_from(page.text)}
        )
        assert r.status_code == 303
    token, _ = await _new_key(client, device_id)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        await pairing.unpair(db, device)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(ObsToken)) == 0
    assert (await client.get(f"/obs/{token}")).status_code == 404


def test_keys_are_never_logged() -> None:
    assert log_path("/obs/AbC_123-x/rahmen") == "/obs/***/rahmen"
    assert log_path("/obs/AbC_123-x") == "/obs/***"
    sid = "01JA" + "X" * 22
    assert log_path(f"/zenivo/{sid}.{'a' * 64}/api/allsky/ingest/frame") == (
        f"/zenivo/{sid}.***/api/allsky/ingest/frame"
    )
    assert log_path("/cameras/x/live") == "/cameras/x/live"


def test_nginx_masks_the_same_paths() -> None:
    """The map in deploy/nginx/allskyhub.conf, its regexes read as Python's."""
    conf = (Path(__file__).parents[2] / "deploy/nginx/allskyhub.conf").read_text()
    block = conf.split("map $uri $allskyhub_logged_uri {")[1].split("}\n")[0]
    rules: list[tuple[re.Pattern[str], str]] = []
    for line in block.strip().splitlines():
        found = re.match(r'\s*"~(.+)" "(.+)";', line)
        if found:
            pattern = re.compile(found[1].replace("(?<", "(?P<"))
            rules.append((pattern, found[2]))
    assert len(rules) == 2

    def logged(uri: str) -> str:
        for pattern, out in rules:
            m = pattern.match(uri)
            if m:
                groups = m.groupdict()
                return re.sub(r"\$\{(\w+)\}", lambda g, d=groups: d[g[1]] or "", out)
        return uri

    assert logged("/obs/AbC_123-x/rahmen") == "/obs/***/rahmen"
    assert logged("/obs/AbC_123-x") == "/obs/***"
    assert logged("/zenivo/01JAX.deadbeef/api/allsky/ingest/frame") == (
        "/zenivo/01JAX.***/api/allsky/ingest/frame"
    )
    assert logged("/cameras/x/live") == "/cameras/x/live"
