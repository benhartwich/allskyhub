"""Clean-up keeps retention as the privacy policy states."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server import maintenance
from allskyhub_server.models import Invitation, WebSession
from tests.helpers import invite, make_account


async def test_purge_removes_old_invitations_and_expired_sessions(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    await make_account(client)  # accepted invitation + live session
    await invite(new_client, "pending@example.org")  # open invitation
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    async def count(model: type[Invitation] | type[WebSession]) -> int:
        async with maker() as db:
            return int(await db.scalar(select(func.count()).select_from(model)) or 0)

    async with maker() as db:
        await maintenance.purge(db)
    assert await count(Invitation) == 1  # the accepted one is gone, the open one stays
    assert await count(WebSession) == 1

    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=7 + 30 + 1)
    async with maker() as db:
        await maintenance.purge(db, now=later)
    assert await count(Invitation) == 0
    assert await count(WebSession) == 0
