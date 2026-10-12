# pyright: reportPrivateUsage=false
"""Map of public cameras (roadmap C5)."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

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
