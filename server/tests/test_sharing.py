"""Sharing a camera with other accounts (roadmap C10)."""

from __future__ import annotations

import re

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.models import CameraMember, User
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, home_csrf, make_account
from tests.ws_helpers import paired_token


async def _user_id(app: FastAPI, email: str) -> str:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        uid = await db.scalar(select(User.id).where(User.email == email))
    assert uid
    return str(uid)


async def test_roles(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    base = f"/cameras/{dev.device_id}"
    friend = await make_account(new_client)
    assert (await new_client.get(base)).status_code == 404  # not shared yet

    data = {"email": "nobody@example.org", "csrf_token": await home_csrf(client)}
    unknown = await client.post(f"{base}/members", data=data)
    assert unknown.status_code == 400
    assert "Es gibt kein Konto mit dieser Adresse." in unknown.text
    data = {"email": friend.upper(), "role": "viewer", "csrf_token": await home_csrf(client)}
    r = await client.post(f"{base}/members", data=data)
    assert r.status_code == 303
    owner_page = (await client.get(base)).text
    assert friend in owner_page

    # Viewer: sees camera, gallery, statistics; may change nothing.
    home = (await new_client.get("/")).text
    assert f'href="{base}"' in home
    assert "geteilt" in home
    page = (await new_client.get(base)).text
    assert "Geteilte Kamera" in page
    for owner_only in ("Kamera entfernen", "Öffentliche Seite einschalten", "Offline-Code",
                       "Einstellungen übernehmen", 'id="teilen"'):  # fmt: skip
        assert owner_only not in page, owner_only
    for path in ("/gallery", "/stats", "/events", "/renders"):
        assert (await new_client.get(base + path)).status_code == 200, path
    csrf = await home_csrf(new_client)
    for path in ("/settings", "/remove", "/public", "/public/edit", "/local-access", "/members"):
        r = await new_client.post(base + path, data={"csrf_token": csrf, "enabled": "true",
                                                     "email": friend})  # fmt: skip
        assert r.status_code in (404, 422), (path, r.status_code)
    params = {"return": "http://x/", "nonce": "n" * 20}
    assert (await new_client.get(f"{base}/local-login", params=params)).status_code == 404

    # Admin: also the settings.
    await client.post(f"{base}/members", data={"email": friend, "role": "admin",
                                               "csrf_token": await home_csrf(client)})  # fmt: skip
    page = (await new_client.get(base)).text
    assert "Einstellungen übernehmen" in page
    assert "Kamera entfernen" not in page

    # The app API follows the same roles.
    login = await new_client.post(
        "/api/v1/auth/login", json={"email": friend, "password": PASSWORD}
    )
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    listed = (await new_client.get("/api/v1/cameras", headers=auth)).json()
    assert [c["id"] for c in listed] == [dev.device_id]
    removed = await new_client.delete(f"/api/v1/cameras/{dev.device_id}", headers=auth)
    assert removed.status_code == 404
    public = await new_client.put(f"/api/v1/cameras/{dev.device_id}/public",
                                  json={"enabled": True}, headers=auth)  # fmt: skip
    assert public.status_code == 404

    # Leaving.
    friend_id = await _user_id(app, friend)
    page = (await new_client.get(base)).text
    assert re.search(rf'action="{base}/members/{friend_id}/remove"', page)
    r = await new_client.post(f"{base}/members/{friend_id}/remove",
                              data={"csrf_token": await home_csrf(new_client)})  # fmt: skip
    assert r.status_code == 303
    assert (await new_client.get(base)).status_code == 404


async def test_unpairing_ends_sharing(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    friend = await make_account(new_client)
    base = f"/cameras/{dev.device_id}"
    await client.post(f"{base}/members", data={"email": friend,
                                               "csrf_token": await home_csrf(client)})  # fmt: skip
    await client.post(f"{base}/remove", data={"csrf_token": await home_csrf(client)})
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        assert (await db.scalars(select(CameraMember))).all() == []
