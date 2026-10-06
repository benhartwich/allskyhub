"""Test helpers: CSRF tokens from pages, sign-up and login."""

from __future__ import annotations

import re
import uuid
from typing import cast

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.auth import invitations

PASSWORD = "correct horse battery"  # noqa: S105
_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(html: str) -> str:
    m = _CSRF_RE.search(html)
    assert m, "no csrf token in page"
    return m.group(1)


async def invite(client: httpx.AsyncClient, email: str) -> str:
    """An invitation as the operator creates it (``allskyhub-server invite``); returns the token."""
    transport = cast(httpx.ASGITransport, client._transport)  # pyright: ignore[reportPrivateUsage]
    app = cast(FastAPI, transport.app)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        token = await invitations.create(db, email)
        await db.commit()
    return token


async def make_account(client: httpx.AsyncClient, email: str | None = None) -> str:
    """Invite an address and accept the invitation in ``client`` (which is then signed in)."""
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.org"
    token = await invite(client, email)
    page = await client.get("/invite", params={"token": token})
    r = await client.post(
        "/invite",
        data={
            "token": token,
            "password": PASSWORD,
            "password2": PASSWORD,
            "csrf_token": csrf_from(page.text),
        },
    )
    assert r.status_code == 303, r.text
    return email


async def home_csrf(client: httpx.AsyncClient) -> str:
    return csrf_from((await client.get("/")).text)


async def claim(client: httpx.AsyncClient, code: str, name: str = "Garten") -> httpx.Response:
    """Pair through the web form (SPEC §6.2 step 3, fallback)."""
    return await client.post(
        "/cameras/pair", data={"code": code, "name": name, "csrf_token": await home_csrf(client)}
    )
