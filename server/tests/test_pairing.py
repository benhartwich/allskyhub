"""Device registration, pairing and tokens (SPEC §6.2, §6.6)."""

from __future__ import annotations

import httpx

from allskyhub_protocol import PAIRING_CODE_ALPHABET, Purpose
from tests.fake_device import FakeDevice
from tests.helpers import claim, home_csrf, make_account


async def test_register_returns_stable_code_until_claimed(client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    first = await dev.register(client)
    assert first.device_id == dev.device_id
    assert not first.paired
    assert first.pairing_code is not None
    assert all(c in PAIRING_CODE_ALPHABET for c in first.pairing_code)
    assert first.expires_in is not None
    assert 0 < first.expires_in <= 15 * 60
    again = await dev.register(client)
    assert again.pairing_code == first.pairing_code


async def test_bad_signature_and_reused_nonce_are_rejected(client: httpx.AsyncClient) -> None:
    dev, other = FakeDevice(), FakeDevice()
    nonce = await dev.nonce(client)
    forged = other.sign(Purpose.REGISTER, nonce)
    r = await client.post("/device/v1/register", json=dev.register_body(nonce, forged))
    assert r.status_code == 401
    # The nonce is spent even though the attempt failed.
    r = await client.post("/device/v1/register", json=dev.register_body(nonce))
    assert r.status_code == 401


async def test_signature_for_other_purpose_is_rejected(client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    nonce = await dev.nonce(client)
    body = dev.register_body(nonce, dev.sign(Purpose.TOKEN, nonce))
    assert (await client.post("/device/v1/register", json=body)).status_code == 401


async def test_nonce_is_bound_to_device(client: httpx.AsyncClient) -> None:
    dev, other = FakeDevice(), FakeDevice()
    nonce = await other.nonce(client)
    r = await client.post("/device/v1/register", json=dev.register_body(nonce))
    assert r.status_code == 401


async def test_token_needs_pairing(client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await dev.register(client)
    r = await dev.token_response(client)
    assert r.status_code == 403


async def test_claim_pairs_device_and_allows_token(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    reg = await dev.register(new_client)
    assert reg.pairing_code
    await make_account(client)
    code = reg.pairing_code
    r = await claim(client, f"{code[:3].lower()}-{code[3:].lower()}", "Garten")
    assert r.status_code == 303
    assert r.headers["location"] == f"/cameras/{dev.device_id}"

    again = await dev.register(new_client)
    assert again.paired
    assert again.pairing_code is None
    assert await dev.token(new_client)

    page = await client.get("/")
    assert "Garten" in page.text


async def test_code_is_single_use_and_wrong_codes_fail(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    reg = await dev.register(new_client)
    assert reg.pairing_code
    await make_account(client)
    assert (await claim(client, "ZZZZZZ")).status_code == 400
    assert (await claim(client, reg.pairing_code)).status_code == 303

    await make_account(new_client)
    r = await claim(new_client, reg.pairing_code)
    assert r.status_code == 400
    assert "ungültig oder abgelaufen" in r.text


async def test_other_accounts_cannot_see_camera(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    reg = await dev.register(client)
    assert reg.pairing_code
    await make_account(client)
    await claim(client, reg.pairing_code)
    await make_account(new_client)
    assert (await new_client.get(f"/cameras/{dev.device_id}")).status_code == 404
    assert (await new_client.get(f"/cameras/{dev.device_id}/image/full.jpg")).status_code == 404
    r = await new_client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(new_client)}
    )
    assert r.status_code == 404


async def test_remove_unpairs_and_revokes_tokens(client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    reg = await dev.register(client)
    assert reg.pairing_code
    await make_account(client)
    await claim(client, reg.pairing_code)
    await dev.token(client)
    r = await client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    assert r.status_code == 303
    assert (await dev.token_response(client)).status_code == 403
    again = await dev.register(client)
    assert not again.paired
    assert again.pairing_code not in (None, reg.pairing_code)
