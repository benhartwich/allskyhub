# pyright: reportPrivateUsage=false
"""Map of public cameras (roadmap C5)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.models import NightStat
from tests.test_public_page import (
    _add_archive,
    _public,
    _save,
)


async def test_only_cameras_that_show_their_place(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)  # reports latitude 48.136, longitude 14.39
    empty = (await new_client.get("/karte")).text
    assert "Noch keine Kamera auf der Karte" in empty
    assert f"/sky/{slug}" not in empty

    await _save(client, device_id, title="Lanzenberg", location_label="Hofkirchen",
                show_coordinates="on")  # fmt: skip
    page = (await new_client.get("/karte")).text
    assert f'href="/sky/{slug}"' in page
    assert "Lanzenberg · Hofkirchen" in page
    # Rounded like on the public page: 48.1° N, 14.4° E → x = 1944, y = 419.
    assert 'cx="1944.0" cy="419.0"' in page
    assert "48.136" not in page
    world = (await new_client.get("/karte?region=welt")).text
    assert 'viewBox="0 0 3600 1800"' in world
    assert 'viewBox="1550 180 700 400"' in (await new_client.get("/karte?region=mars")).text
    assert 'href="/karte"' in page  # in the footer of every page


async def test_sky_brightness_layer(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """Roadmap H4: median of the darkest reading of clear nights, only when the public
    page shows the sky quality."""
    device_id, _ = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    today = dt.datetime.now(dt.UTC).date()
    async with maker() as db:
        for i, (sqm, clear) in enumerate(
            [(21.4, 5.0), (21.5, 4.0), (21.6, 3.0), (18.0, 0.5), (19.0, None)]
        ):
            night = (today - dt.timedelta(days=i + 1)).strftime("%Y%m%d")
            db.add(NightStat(device_id=device_id, night_id=night, best_sqm=sqm,
                             clear_hours=clear))  # fmt: skip
        old = (today - dt.timedelta(days=90)).strftime("%Y%m%d")
        db.add(NightStat(device_id=device_id, night_id=old, best_sqm=15.0, clear_hours=8.0))
        await db.commit()
    await _save(client, device_id, title="Lanzenberg", show_coordinates="on", section_sky="on")
    page = (await new_client.get("/karte?layer=sqm")).text
    assert 'class="dot sqm-2"' in page  # median 21.5: cloudy and old nights left out
    assert "21,50 mag/″²" in page
    assert "Himmelshelligkeit" in page
    plain = (await new_client.get("/karte")).text
    assert 'class="dot"' in plain

    await _save(client, device_id, title="Lanzenberg", show_coordinates="on")  # sky off
    page = (await new_client.get("/karte?layer=sqm")).text
    assert 'class="dot sqm-none"' in page
    assert "21,50" not in page
