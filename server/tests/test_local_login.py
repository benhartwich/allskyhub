"""Local login through the hub (SPEC §6.2 step 6, §6.5 local_access, §6.8, §7.4)."""

from __future__ import annotations

import asyncio
import re
import stat
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import (
    Ack,
    Command,
    CommandName,
    Hello,
    LocalAccessArgs,
    TokenResponse,
    b64url_decode,
    local_login_signing_payload,
    normalize_offline_code,
    parse_envelope,
    split_local_login_assertion,
)
from allskyhub_server.models import Device
from allskyhub_server.web.routes_local import looks_local, return_base
from tests.fake_device import FakeDevice
from tests.helpers import home_csrf, make_account
from tests.ws_helpers import device_ws, next_command, paired_token, send

NONCE = "n0nce-" + "A" * 30


async def _device(app: FastAPI, device_id: str) -> Device:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        return device


async def test_token_answer_carries_hub_keys(app: FastAPI, client: httpx.AsyncClient) -> None:
    """SPEC §6.2 step 6: the key is made once (0600) and announced with every token."""
    dev = FakeDevice()
    await paired_token(client, dev)
    first = TokenResponse.model_validate((await dev.token_response(client)).json())
    second = TokenResponse.model_validate((await dev.token_response(client)).json())
    assert len(first.hub_keys) == 1
    assert first.hub_keys == second.hub_keys
    files = list(app.state.settings.key_dir.glob("*.key"))
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("value", "base"),
    [
        ("http://192.168.1.20:8080", "http://192.168.1.20:8080/"),
        ("https://cam.example.org/hub/", "https://cam.example.org/hub/"),
        ("javascript:alert(1)", None),
        ("http://user:pw@cam.example.org/", None),
        ("http://cam.example.org/?x=1", None),
        ("http://cam.example.org/#x", None),
        ("//cam.example.org/", None),
        ("http://cam.example.org:99999/", None),
        ('http://cam.example.org/"><script>', None),
        ("http://" + "a" * 500 + ".org/", None),
    ],
)
def test_return_base(value: str, base: str | None) -> None:
    """SPEC §7.4 A step 2."""
    assert return_base(value) == base


@pytest.mark.parametrize(
    ("base", "local"),
    [
        ("http://192.168.1.20:8080/", True),
        ("http://[fe80::1]/", True),
        ("http://100.101.2.3/", True),
        ("http://allskyhub.local/", True),
        ("https://garten.fritz.box/", True),
        ("http://allskyhub/", True),
        ("https://cam.example.org/", False),
        ("http://8.8.8.8/", False),
    ],
)
def test_looks_local(base: str, local: bool) -> None:
    """Audit L6: addresses outside a home network get a warning."""
    assert looks_local(base) is local


async def test_single_sign_on(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """SPEC §7.4 A: login on the hub if needed, confirm, back with a signed assertion."""
    dev = FakeDevice()
    token = await paired_token(client, dev)
    keys = TokenResponse.model_validate((await dev.token_response(client)).json()).hub_keys
    url = f"/cameras/{dev.device_id}/local-login"
    query = {"return": "http://192.168.1.20:8080/", "nonce": NONCE}
    del token

    anon = await new_client.get(url, params=query)
    assert anon.status_code == 303
    target = parse_qs(urlsplit(anon.headers["location"]).query)["next"][0]
    assert target.startswith(url + "?")  # the query survives the login
    assert NONCE in target

    page = await client.get(url, params=query)
    assert page.status_code == 200
    assert "http://192.168.1.20:8080" in page.text
    assert "nicht in einem Heimnetz" not in page.text
    far = await client.get(url, params={**query, "return": "https://cam.example.org/"})
    assert "nicht in einem Heimnetz" in far.text
    # The form is redirected to the camera, so its origin must be an allowed form target.
    assert "form-action 'self' http://192.168.1.20:8080" in page.headers["content-security-policy"]
    assert (await client.get(url, params={**query, "return": "ftp://x/"})).status_code == 400
    assert (await client.get(url, params={**query, "nonce": "short"})).status_code == 400

    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf
    r = await client.post(url, data={"csrf_token": csrf[1], **query})
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("http://192.168.1.20:8080/local-login/callback#assertion=")
    assertion = location.split("#assertion=", 1)[1]
    payload, claims, signature = split_local_login_assertion(assertion)
    assert claims.aud == dev.device_id
    assert claims.nonce == NONCE
    assert claims.exp - claims.iat == 60
    key = next(k for k in keys if k.kid == claims.kid)
    Ed25519PublicKey.from_public_bytes(b64url_decode(key.public_key)).verify(
        signature, local_login_signing_payload(payload)
    )

    # Someone else's camera: 404, also for the confirmation.
    await make_account(new_client)
    assert (await new_client.get(url, params=query)).status_code == 404
    other = await new_client.post(url, data={"csrf_token": await home_csrf(new_client), **query})
    assert other.status_code == 404


async def test_offline_code_reaches_the_camera(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    """SPEC §7.4 B and §6.5 local_access: shown once, verifier only, resent after hello."""
    dev = FakeDevice()
    token = await paired_token(client, dev)
    page = await client.post(
        f"/cameras/{dev.device_id}/local-access", data={"csrf_token": await home_csrf(client)}
    )
    shown = re.search(r"([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})", page.text)
    assert shown
    code = normalize_offline_code(shown[1])
    device = await _device(app, dev.device_id)
    assert device.local_access_pending
    assert device.local_access_verifier
    assert code not in device.local_access_verifier
    assert PasswordHasher().verify(device.local_access_verifier, code)
    camera = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "noch nicht bei der Kamera" in camera

    async with device_ws(live_app, token) as ws:
        await send(ws, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="1"))
        env = parse_envelope(await asyncio.wait_for(ws.recv(), 5))
        assert isinstance(env.body, Command)
        assert env.body.name == CommandName.LOCAL_ACCESS
        args = LocalAccessArgs.model_validate(env.body.args)
        assert args.verifier == device.local_access_verifier
        await send(ws, Ack(ref=env.id))
        for _ in range(100):
            if not (await _device(app, dev.device_id)).local_access_pending:
                break
            await asyncio.sleep(0.05)
        assert not (await _device(app, dev.device_id)).local_access_pending

        # Removing it while online reaches the camera at once.
        await client.post(
            f"/cameras/{dev.device_id}/local-access/remove",
            data={"csrf_token": await home_csrf(client)},
        )
        cmd = await next_command(ws)
        assert LocalAccessArgs.model_validate(cmd.args).verifier is None


async def test_public_page_from_the_camera(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """SPEC §6.8: GET/PUT /device/v1/public with the device token."""
    dev = FakeDevice()
    token = await paired_token(client, dev)
    auth = {"Authorization": f"Bearer {token}"}
    url = "/device/v1/public"
    assert (await new_client.get(url)).status_code == 401
    state = (await new_client.get(url, headers=auth)).json()
    assert state["enabled"] is False
    assert state["url"] is None
    assert state["profile"]["sections"]["live"] is True

    on = (await new_client.put(url, json={"enabled": True}, headers=auth)).json()
    assert on["url"].startswith("http://testserver/sky/")
    profile = {"title": "Sternwarte", "sections": {"loop": False}}
    r = await new_client.put(url, json={"profile": profile}, headers=auth)
    assert r.json()["url"] == on["url"]  # only the profile changed
    assert r.json()["profile"]["title"] == "Sternwarte"
    assert r.json()["profile"]["sections"]["loop"] is False
    page = (await new_client.get(on["url"].removeprefix("http://testserver"))).text
    assert "Sternwarte" in page
    assert "Die letzte Stunde" not in page

    too_long = await new_client.put(url, json={"profile": {"title": "x" * 101}}, headers=auth)
    assert too_long.status_code == 422
    assert too_long.json()["detail"] == "Titel ist zu lang (höchstens 100 Zeichen)."
    unknown = await new_client.put(url, json={"profile": {"colour": "red"}}, headers=auth)
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == "colour gibt es nicht."
    off = (await new_client.put(url, json={"enabled": False}, headers=auth)).json()
    assert off["url"] is None
    assert off["profile"]["title"] == "Sternwarte"


async def test_unpairing_drops_local_access(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    await client.post(
        f"/cameras/{dev.device_id}/local-access", data={"csrf_token": await home_csrf(client)}
    )
    await client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    device = await _device(app, dev.device_id)
    assert device.local_access_verifier is None
    assert not device.local_access_pending
