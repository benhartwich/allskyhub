"""App API: sharing (roadmap C10), public page (#9, SPEC §6.8), statistics (C7), exports (C11)."""

from __future__ import annotations

import httpx

from tests.fake_device import FakeDevice
from tests.helpers import claim, make_account
from tests.test_app_api import _app_login  # pyright: ignore[reportPrivateUsage]


async def _camera(client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    dev = FakeDevice()
    reg = await dev.register(client)
    assert reg.pairing_code
    email = await make_account(client)
    await claim(client, reg.pairing_code, "Garten")
    return dev.device_id, await _app_login(client, email)


async def test_sharing(client: httpx.AsyncClient, new_client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(client)
    friend = await make_account(new_client)
    friend_auth = await _app_login(new_client, friend)
    base = f"/api/v1/cameras/{device_id}"

    r = await client.post(f"{base}/members", json={"email": "nobody@example.org"}, headers=auth)
    assert r.status_code == 400
    assert r.json()["detail"] == "Es gibt kein Konto mit dieser Adresse."

    r = await client.post(f"{base}/members", json={"email": friend.upper()}, headers=auth)
    assert r.status_code == 200, r.text
    assert [(m["email"], m["role"]) for m in r.json()] == [(friend, "viewer")]

    cams = (await new_client.get("/api/v1/cameras", headers=friend_auth)).json()
    assert [(c["id"], c["role"]) for c in cams] == [(device_id, "viewer")]
    # A viewer sees neither the members nor the public page.
    assert (await new_client.get(f"{base}/members", headers=friend_auth)).status_code == 404
    assert (await new_client.get(f"{base}/public/profile", headers=friend_auth)).status_code == 404

    r = await client.post(f"{base}/members", json={"email": friend, "role": "admin"}, headers=auth)
    assert r.json()[0]["role"] == "admin"
    friend_id = r.json()[0]["user_id"]
    other = (await client.get("/api/v1/account/me", headers=auth)).json()["user_id"]
    # A member cannot remove the owner or anyone else, only leave.
    r = await new_client.delete(f"{base}/members/{other}", headers=friend_auth)
    assert r.status_code == 404
    me = (await new_client.get("/api/v1/account/me", headers=friend_auth)).json()
    assert me["user_id"] == friend_id
    r = await new_client.delete(f"{base}/members/{friend_id}", headers=friend_auth)
    assert r.status_code == 204
    assert (await new_client.get("/api/v1/cameras", headers=friend_auth)).json() == []
    assert (await client.get(f"{base}/members", headers=auth)).json() == []


async def test_public_profile(client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(client)
    url = f"/api/v1/cameras/{device_id}/public/profile"
    state = (await client.get(url, headers=auth)).json()
    assert state["enabled"] is False
    assert state["url"] is None
    assert state["profile"]["sections"]["live"] is True

    profile = {**state["profile"], "title": "Sternwarte", "sections": {"observatory": True}}
    r = await client.put(url, json={"enabled": True, "profile": profile}, headers=auth)
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["enabled"] is True
    assert state["url"].startswith("http")
    assert state["profile"]["title"] == "Sternwarte"
    assert state["profile"]["sections"]["observatory"] is True
    assert state["on_home"] is False

    # The start page: the hub's own switch, beside the profile.
    r = await client.put(url, json={"on_home": True}, headers=auth)
    assert r.json()["on_home"] is True
    assert "on_home" not in r.json()["profile"]
    assert r.json()["profile"]["title"] == "Sternwarte"

    r = await client.put(url, json={"profile": {"title": "x" * 101}}, headers=auth)
    assert r.status_code == 422
    assert r.json()["detail"] == "Titel ist zu lang (höchstens 100 Zeichen)."
    r = await client.put(url, json={"enabled": False}, headers=auth)
    assert r.json()["enabled"] is False
    assert r.json()["profile"]["title"] == "Sternwarte"


async def test_stats_and_exports(client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(client)
    base = f"/api/v1/cameras/{device_id}"
    r = await client.get(f"{base}/stats?year=2026", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["year"] == 2026
    assert len(body["months"]) == 12
    assert body["total"]["nights"] == 0

    listed = (await client.get(f"{base}/exports", headers=auth)).json()
    assert [e["name"] for e in listed] == ["meteors", "meteors-hourly", "sqm"]
    r = await client.get(f"{base}/exports/sqm.csv", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "sqm" in r.headers["content-disposition"]
    assert (await client.get(f"{base}/exports/nope.csv", headers=auth)).status_code == 404


async def test_network_switch(client: httpx.AsyncClient) -> None:
    """Audit L2: the owner joins and leaves the network of other owners' cameras."""
    device_id, auth = await _camera(client)
    url = f"/api/v1/cameras/{device_id}/network"
    assert (await client.get(url, headers=auth)).json() == {"network": False}
    assert (await client.put(url, json={"network": True}, headers=auth)).json() == {"network": True}
    assert (await client.get(url, headers=auth)).json() == {"network": True}
    page = (await client.get(f"/cameras/{device_id}")).text
    assert 'name="network" checked' in page
