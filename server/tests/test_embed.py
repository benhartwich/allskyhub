"""Widget, image link and Atom feed of a public page (roadmap C9), aurora map (C13)."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.models import EventRecord
from tests.conftest import OVATION_JPEG
from tests.test_public_page import _public, _save  # pyright: ignore[reportPrivateUsage]

ATOM = "{http://www.w3.org/2005/Atom}"


async def test_widget_may_be_framed(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    widget = await new_client.get(f"/sky/{slug}/widget")
    assert widget.status_code == 200
    assert "frame-ancestors *" in widget.headers["content-security-policy"]
    assert f'src="/sky/{slug}/latest.jpg' in widget.text
    page = await new_client.get(f"/sky/{slug}")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    edit = (await client.get(f"/cameras/{device_id}/public/edit")).text
    assert f"/sky/{slug}/widget" in edit
    assert f"/sky/{slug}/events.atom" in edit


async def test_atom_feed(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _save(client, device_id, title="Lanzenberg <&>", section_events="on")
    now = dt.datetime.now(dt.UTC)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(EventRecord(device_id=device_id, event_id="meteor-1", night_id="20261012",
                           kind="meteor", start=now, end=now, confidence=0.9, has_image=True,
                           has_thumb=True, data={}))  # fmt: skip
        await db.commit()
    r = await new_client.get(f"/sky/{slug}/events.atom")
    assert r.headers["content-type"].startswith("application/atom+xml")
    feed = ET.fromstring(r.content)  # noqa: S314 - our own output
    title = feed.find(f"{ATOM}title")
    assert title is not None
    assert title.text == "Lanzenberg <&>: Ereignisse am Himmel"
    entries = feed.findall(f"{ATOM}entry")
    assert len(entries) == 1
    assert "/events/20261012/meteor-1.jpg" in r.text
    page = (await new_client.get(f"/sky/{slug}")).text
    assert f'href="/sky/{slug}/events.atom"' in page

    await _save(client, device_id, section_live="on")  # events off
    assert (await new_client.get(f"/sky/{slug}/events.atom")).status_code == 404


async def test_aurora_map_through_the_hub(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    _, slug = await _public(app, client, new_client)
    page = (await new_client.get(f"/sky/{slug}")).text
    assert 'src="/space-weather/ovation/north.jpg"' in page
    r = await new_client.get("/space-weather/ovation/north.jpg")
    assert r.content == OVATION_JPEG
    assert r.headers["cache-control"] == "public, max-age=600"
    assert (await new_client.get("/space-weather/ovation/east.jpg")).status_code == 404
