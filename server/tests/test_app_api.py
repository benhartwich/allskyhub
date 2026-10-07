"""App API v1 (M3): token login, claiming by code, cameras, images."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices.images import ImageStore
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, make_account

JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 32


async def _app_login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD, "label": "Pixel"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_login_rejects_wrong_password(client: httpx.AsyncClient) -> None:
    email = await make_account(client)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "nope"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Wrong email or password"}


async def test_requires_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/cameras")).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert (await client.get("/api/v1/cameras", headers=bad)).status_code == 401


async def test_claim_list_image_remove(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    email = await make_account(client)
    auth = await _app_login(new_client, email)
    dev = FakeDevice()
    reg = await dev.register(new_client)
    assert reg.pairing_code

    assert (await new_client.get("/api/v1/cameras", headers=auth)).json() == []
    wrong = await new_client.post("/api/v1/cameras/claim", json={"code": "ZZZZZZ"}, headers=auth)
    assert wrong.status_code == 400
    r = await new_client.post(
        "/api/v1/cameras/claim", json={"code": reg.pairing_code, "name": "Dach"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == dev.device_id
    assert r.json()["name"] == "Dach"
    assert not r.json()["online"]

    cams = (await new_client.get("/api/v1/cameras", headers=auth)).json()
    assert [c["id"] for c in cams] == [dev.device_id]
    url = f"/api/v1/cameras/{dev.device_id}/image/full.jpg"
    assert (await new_client.get(url, headers=auth)).status_code == 404

    store: ImageStore = app.state.images
    store.save_frame(dev.device_id, "20261006", "a.jpg", FrameVariant.FULL, JPEG)
    img = await new_client.get(url + "?live=true", headers=auth)
    assert img.content == JPEG
    assert app.state.connections.is_live(dev.device_id)

    r = await new_client.delete(f"/api/v1/cameras/{dev.device_id}", headers=auth)
    assert r.status_code == 204
    assert (await new_client.get("/api/v1/cameras", headers=auth)).json() == []
    assert (await dev.token_response(new_client)).status_code == 403


async def test_other_account_gets_404(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    owner = await _app_login(client, await make_account(client))
    other = await _app_login(new_client, await make_account(new_client))
    dev = FakeDevice()
    reg = await dev.register(client)
    assert reg.pairing_code
    await client.post("/api/v1/cameras/claim", json={"code": reg.pairing_code}, headers=owner)
    assert (
        await new_client.get(f"/api/v1/cameras/{dev.device_id}", headers=other)
    ).status_code == 404
    r = await new_client.delete(f"/api/v1/cameras/{dev.device_id}", headers=other)
    assert r.status_code == 404


async def test_logout_revokes_token(client: httpx.AsyncClient) -> None:
    auth = await _app_login(client, await make_account(client))
    assert (await client.post("/api/v1/auth/logout", headers=auth)).status_code == 204
    assert (await client.get("/api/v1/cameras", headers=auth)).status_code == 401
