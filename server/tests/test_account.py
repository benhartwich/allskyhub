"""Self-service account (roadmap #10): change the password, delete the account."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from allskyhub_protocol import CloseCode, FrameVariant
from allskyhub_server.devices.images import ImageStore
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, claim, csrf_from, make_account
from tests.ws_helpers import JPEG, closed_with, device_ws, paired_token

NEW = "a brand new password"


async def _csrf(client: httpx.AsyncClient) -> str:
    return csrf_from((await client.get("/account")).text)


async def _login(client: httpx.AsyncClient, email: str, password: str) -> int:
    page = await client.get("/login")
    r = await client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf_from(page.text)},
    )
    return r.status_code


async def test_change_password_keeps_this_session_and_ends_the_others(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    email = await make_account(client)
    assert await _login(new_client, email, PASSWORD) == 303  # a second browser
    app_login = await new_client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    app_auth = {"Authorization": f"Bearer {app_login.json()['access_token']}"}

    page = await client.get("/account")
    assert email in page.text
    wrong = await client.post(
        "/account/password",
        data={"current": "wrong one", "password": NEW, "password2": NEW,
              "csrf_token": csrf_from(page.text)},
    )  # fmt: skip
    assert wrong.status_code == 400
    assert "stimmt nicht" in wrong.text
    ok = await client.post(
        "/account/password",
        data={"current": PASSWORD, "password": NEW, "password2": NEW,
              "csrf_token": await _csrf(client)},
    )  # fmt: skip
    assert ok.status_code == 200
    assert "Passwort geändert" in ok.text

    assert "Meine Kameras" in (await client.get("/")).text  # still signed in here
    assert "Meine Kameras" not in (await new_client.get("/")).text  # the other browser not
    assert (await new_client.get("/api/v1/cameras", headers=app_auth)).status_code == 401
    assert await _login(new_client, email, PASSWORD) == 400
    assert await _login(new_client, email, NEW) == 303


async def test_delete_account_unpairs_cameras_and_removes_everything(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)  # signs up and pairs
    store: ImageStore = app.state.images
    store.save_frame(dev.device_id, "20261007", "a.jpg", FrameVariant.FULL, JPEG)

    async with device_ws(live_app, token) as ws:
        refused = await client.post(
            "/account/delete", data={"current": PASSWORD, "csrf_token": await _csrf(client)}
        )
        assert refused.status_code == 400  # not confirmed
        r = await client.post(
            "/account/delete",
            data={"current": PASSWORD, "confirm": "true", "csrf_token": await _csrf(client)},
        )
        assert r.status_code == 303
        assert await closed_with(ws) == CloseCode.UNPAIRED

    assert not (store.root / dev.device_id).exists()
    assert "Meine Kameras" not in (await client.get("/")).text
    assert not (await dev.register(client)).paired


async def test_app_api_account(app: FastAPI, client: httpx.AsyncClient) -> None:
    email = await make_account(client)
    dev = FakeDevice()
    reg = await dev.register(client)
    assert reg.pairing_code
    await claim(client, reg.pairing_code)
    first = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    second = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    me = {"Authorization": f"Bearer {first.json()['access_token']}"}
    other = {"Authorization": f"Bearer {second.json()['access_token']}"}

    bad = await client.post(
        "/api/v1/account/password", json={"current": "nope", "new": NEW}, headers=me
    )
    assert bad.status_code == 400
    short = await client.post(
        "/api/v1/account/password", json={"current": PASSWORD, "new": "short"}, headers=me
    )
    assert short.status_code == 400
    ok = await client.post(
        "/api/v1/account/password", json={"current": PASSWORD, "new": NEW}, headers=me
    )
    assert ok.status_code == 204
    assert (await client.get("/api/v1/cameras", headers=me)).status_code == 200
    assert (await client.get("/api/v1/cameras", headers=other)).status_code == 401

    wrong = await client.post("/api/v1/account/delete", json={"password": PASSWORD}, headers=me)
    assert wrong.status_code == 400  # the old password no longer works
    gone = await client.post("/api/v1/account/delete", json={"password": NEW}, headers=me)
    assert gone.status_code == 204
    assert (await client.get("/api/v1/cameras", headers=me)).status_code == 401
    assert not (await dev.register(client)).paired
