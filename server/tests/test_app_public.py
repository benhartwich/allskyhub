# pyright: reportPrivateUsage=false
"""Public cameras for the app without login: only what the public page shows."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from tests.test_public_page import _add_archive, _public, _save

ALL_SECTIONS = {f"section_{s}": "on" for s in ("live", "loop", "archive", "events", "sky")}


async def test_listed_only_on_home_or_map(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    # A page shared only by link is not listed, but opens by its slug like the page.
    assert (await new_client.get("/api/v1/public/cameras")).json() == []
    assert (await new_client.get(f"/api/v1/public/cameras/{slug}")).status_code == 200

    await _save(client, device_id, title="Sternwarte", location_label="Musterdorf",
                show_coordinates="on", **ALL_SECTIONS)  # fmt: skip
    r = await new_client.get("/api/v1/public/cameras")
    assert r.headers["cache-control"] == "public, max-age=60"
    [camera] = r.json()
    assert camera["slug"] == slug
    assert (camera["title"], camera["place"]) == ("Sternwarte", "Musterdorf")
    assert (camera["lat"], camera["lon"]) == (50.0, 10.4)
    assert "50.036" not in r.text
    assert device_id not in r.text

    await _save(client, device_id, title="Sternwarte", on_home="on", **ALL_SECTIONS)
    [camera] = (await new_client.get("/api/v1/public/cameras")).json()
    assert camera["lat"] is None  # on the start page, but the place stays hidden


async def test_detail_follows_the_sections(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    await _save(client, device_id, title="Sternwarte", camera="ZWO ASI678MC", on_home="on",
                **ALL_SECTIONS)  # fmt: skip
    detail = (await new_client.get(f"/api/v1/public/cameras/{slug}")).json()
    assert detail["camera"] == "ZWO ASI678MC"
    assert device_id not in str(detail)
    assert [f["url"] for f in detail["loop"]][-1] == f"/sky/{slug}/frames/20261012/image-0.jpg"
    assert [e["url"] for e in detail["events"]] == [f"/sky/{slug}/events/20261012/meteor-1.jpg?v=1"]
    assert detail["events"][0]["title"] == "Meteor"
    assert "20261012" in detail["nights"]
    for event in detail["events"]:
        assert (await new_client.get(event["url"])).status_code == 200

    feed = (await new_client.get("/api/v1/public/feed")).json()["events"]
    assert [e["url"] for e in feed] == [f"/sky/{slug}/events/20261012/meteor-1.jpg?v=1"]

    await _save(client, device_id, title="Sternwarte", on_home="on")  # all sections off
    detail = (await new_client.get(f"/api/v1/public/cameras/{slug}")).json()
    assert (detail["loop"], detail["events"], detail["nights"]) == ([], [], [])
    assert (detail["live_url"], detail["facts"], detail["image_at"]) == (None, None, None)
    assert (detail["online"], detail["night"]) == (None, None)
    assert (await new_client.get("/api/v1/public/feed")).json() == {"events": []}


async def test_unknown_or_switched_off(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    assert (await new_client.get("/api/v1/public/cameras/nope")).status_code == 404
    device_id, slug = await _public(app, client, new_client)
    await _save(client, device_id, on_home="on")
    from tests.test_public import _toggle

    await _toggle(client, device_id, False)
    assert (await new_client.get(f"/api/v1/public/cameras/{slug}")).status_code == 404
    assert (await new_client.get("/api/v1/public/cameras")).json() == []
