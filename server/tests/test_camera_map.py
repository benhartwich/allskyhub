# pyright: reportPrivateUsage=false
"""Map of public cameras (roadmap C5), and public cameras on the start page."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices.map_tiles import MapTiles
from allskyhub_server.models import NightStat
from tests.helpers import home_csrf
from tests.test_public_page import (
    _add_archive,
    _public,
    _save,
)


async def test_only_cameras_that_show_their_place(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)  # reports latitude 48.306, longitude 14.29
    empty = (await new_client.get("/karte")).text
    assert "Noch keine Kamera auf der Karte" in empty
    assert f"/sky/{slug}" not in empty

    await _save(client, device_id, title="Pöstlingberg", location_label="Linz",
                show_coordinates="on")  # fmt: skip
    page = (await new_client.get("/karte")).text
    assert f'href="/sky/{slug}"' in page
    assert "Pöstlingberg" in page
    assert "Linz" in page
    # Rounded like on the public page: 48.3° N, 14.3° E, in the map's data.
    config = json.loads(page.split('id="map-config">')[1].split("</script>")[0])
    camera = config["cameras"][0]
    assert (camera["lat"], camera["lon"], camera["slug"]) == (48.3, 14.3, slug)
    assert "48.306" not in page
    assert config["view"] == [50.0, 12.0, 4]
    world = (await new_client.get("/karte?region=welt")).text
    assert '"view": [25.0, 10.0, 2]' in world
    assert '"view": [50.0, 12.0, 4]' in (await new_client.get("/karte?region=mars")).text
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
    await _save(client, device_id, title="Pöstlingberg", show_coordinates="on", section_sky="on")
    page = (await new_client.get("/karte?layer=sqm")).text
    camera = _config(page)["cameras"][0]
    assert camera["sqm_class"] == "sqm-2"  # median 21.5: cloudy and old nights left out
    assert camera["sqm_text"] == "21,50 mag/″² · dunkel"
    assert _config(page)["layer"] == "sqm"
    assert "Nachtlichter" in page
    assert _config((await new_client.get("/karte")).text)["layer"] == ""

    await _save(client, device_id, title="Pöstlingberg", show_coordinates="on")  # sky off
    page = (await new_client.get("/karte?layer=sqm")).text
    camera = _config(page)["cameras"][0]
    assert camera["sqm_class"] == "sqm-none"
    assert camera["sqm_text"] == "Himmelsqualität nicht öffentlich"
    assert "21,50" not in page


def _config(page: str) -> dict[str, Any]:
    found: dict[str, Any] = json.loads(page.split('id="map-config">')[1].split("</script>")[0])
    return found


class _FakeServices:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.host == "nominatim.openstreetmap.org":
            assert request.url.params["q"] == "Linz"
            place = {"display_name": "Linz, Oberösterreich", "lat": "48.3", "lon": "14.29",
                     "boundingbox": ["48.2", "48.4", "14.2", "14.4"]}  # fmt: skip
            return httpx.Response(200, json=[place])
        return httpx.Response(200, content=b"\x89PNG fake tile")


async def test_tiles_and_search_through_the_hub(tmp_path: Path) -> None:
    fake = _FakeServices()
    tiles = MapTiles(tmp_path, httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    assert await tiles.tile("karte", 5, 17, 11) == b"\x89PNG fake tile"
    assert await tiles.tile("karte", 5, 17, 11) == b"\x89PNG fake tile"  # from disk
    assert fake.paths == ["/5/17/11.png"]
    assert await tiles.tile("karte", 12, 0, 0) is None  # deeper than the cameras' rounding
    assert await tiles.tile("nacht", 9, 0, 0) is None
    assert await tiles.tile("karte", 2, 4, 0) is None  # outside the grid
    assert await tiles.tile("google", 1, 0, 0) is None
    assert await tiles.tile("satellit", 3, 1, 2) is not None
    assert fake.paths[-1].endswith("/g/3/2/1.jpg")  # EOX: row before column

    places = await tiles.search("  Linz ")
    assert places == [{"name": "Linz, Oberösterreich", "lat": 48.3, "lon": 14.29,
                       "bbox": [48.2, 48.4, 14.2, 14.4]}]  # fmt: skip
    assert await tiles.search("linz") == places  # cached, no second request
    assert fake.paths.count("/search") == 1
    assert await tiles.search("x") == []


async def test_tile_route(new_client: httpx.AsyncClient) -> None:
    assert (await new_client.get("/karte/kacheln/karte/20/0/0")).status_code == 404
    assert (await new_client.get("/karte/kacheln/google/1/0/0")).status_code == 404


async def test_start_page_lists_only_cameras_whose_owner_wants_it(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    landing = (await new_client.get("/")).text
    assert 'href="/karte"' in landing.split("<main>")[0]  # in the header for visitors
    assert "Öffentliche Kameras" not in landing
    assert f"/sky/{slug}" not in landing

    await _save(client, device_id, title="Pöstlingberg", location_label="Linz",
                on_home="on")  # fmt: skip
    edit = (await client.get(f"/cameras/{device_id}/public/edit")).text
    assert 'name="on_home" checked' in edit
    landing = (await new_client.get("/")).text
    assert "Öffentliche Kameras" in landing
    assert f'href="/sky/{slug}"' in landing
    assert "Pöstlingberg" in landing
    assert "Linz" in landing
    assert "Noch keine Kamera" in (await new_client.get("/karte")).text  # no coordinates

    await _save(client, device_id, title="Pöstlingberg")  # unticked: off again
    assert f"/sky/{slug}" not in (await new_client.get("/")).text
    await _save(client, device_id, on_home="on")
    await client.post(  # the page switched off: gone from the start page too
        f"/cameras/{device_id}/public",
        data={"csrf_token": await home_csrf(client), "enabled": "false"},
    )
    assert "Öffentliche Kameras" not in (await new_client.get("/")).text


async def test_switches_on_the_camera_page(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """Map and start page right in the "Öffentliche Seite" box, texts untouched."""
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    await _save(client, device_id, title="Pöstlingberg")
    page = (await client.get(f"/cameras/{device_id}")).text
    assert f'action="/cameras/{device_id}/public/visibility"' in page
    r = await client.post(
        f"/cameras/{device_id}/public/visibility",
        data={"csrf_token": await home_csrf(client), "show_coordinates": "on", "on_home": "on"},
    )
    assert r.status_code == 303
    assert f'href="/sky/{slug}"' in (await new_client.get("/karte")).text
    assert f'href="/sky/{slug}"' in (await new_client.get("/")).text
    edit = (await client.get(f"/cameras/{device_id}/public/edit")).text
    assert 'value="Pöstlingberg"' in edit
    assert 'name="show_coordinates" checked' in edit

    await client.post(
        f"/cameras/{device_id}/public/visibility", data={"csrf_token": await home_csrf(client)}
    )
    assert "Noch keine Kamera" in (await new_client.get("/karte")).text
    assert f"/sky/{slug}" not in (await new_client.get("/")).text


async def test_signed_in_stays_signed_in_on_public_pages(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """The header shows the account on the map and the public pages, not "Anmelden"."""
    _, slug = await _public(app, client, new_client)
    for url in ("/karte", f"/sky/{slug}"):
        signed_in = (await client.get(url)).text.split("<main>")[0]
        assert 'href="/account"' in signed_in, url
        assert 'href="/login"' not in signed_in, url
        visitor = (await new_client.get(url)).text.split("<main>")[0]
        assert 'href="/login"' in visitor, url
