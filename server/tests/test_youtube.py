"""The night's timelapse on the owner's YouTube channel (roadmap H5)."""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.admin import stores
from allskyhub_server.devices import youtube
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, Product
from tests.helpers import home_csrf
from tests.test_alarms import _set, _setup  # pyright: ignore[reportPrivateUsage]

BASE = "http://testserver"
CLIENT = {
    "web": {
        "client_id": "123-abc.apps.googleusercontent.com",
        "client_secret": "GOCSPX-test",
        "project_id": "allskyhub",
        "redirect_uris": [f"{BASE}/youtube/callback"],
    }
}
MP4 = b"\x00\x00\x00\x18ftypmp42 video"


class Google:
    """Google's token, revoke and upload endpoints, recording what the hub sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []
        self.refresh_error: str | None = None
        self.uploaded: bytes = b""

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = request.read()
        self.calls.append((request.method, url.split("?")[0], body))
        if url == youtube.TOKEN:
            form = urllib.parse.parse_qs(body.decode())
            if form["grant_type"] == ["authorization_code"]:
                return httpx.Response(
                    200, json={"refresh_token": "1//refresh", "scope": youtube.SCOPE}
                )
            if self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error})
            return httpx.Response(200, json={"access_token": "ya29.access"})
        if url == youtube.REVOKE:
            return httpx.Response(200)
        if url.startswith(youtube.UPLOAD):
            assert request.headers["authorization"] == "Bearer ya29.access"
            return httpx.Response(200, headers={"location": "https://upload.example/session/1"})
        if url == "https://upload.example/session/1":
            self.uploaded = body
            return httpx.Response(
                200, json={"id": "dQw4w9WgXcQ", "snippet": {"channelTitle": "Sternwarte"}}
            )
        return httpx.Response(404)


def _vault(app: FastAPI) -> stores.Vault:
    vault: stores.Vault = app.state.stores.vault
    vault.put("youtube", json.dumps(CLIENT).encode(), {}, "admin@example.org")
    return vault


def _service(app: FastAPI, google: Google) -> youtube.YouTube:
    service: youtube.YouTube = app.state.youtube
    service._http = httpx.AsyncClient(transport=httpx.MockTransport(google.handler))  # pyright: ignore[reportPrivateUsage]
    return service


async def _timelapse(app: FastAPI, device_id: str, night_id: str, *, full: bool) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(Product(device_id=device_id, night_id=night_id, kind="timelapse",
                       name="timelapse.mp4", content_type="video/mp4", size=len(MP4),
                       thumbnail=True, has_full=full))  # fmt: skip
        await db.commit()
    if full:
        store: ImageStore = app.state.images
        path = store.product_path(device_id, night_id, "timelapse.mp4", FrameVariant.FULL)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MP4)


async def _video(app: FastAPI, device_id: str, night_id: str) -> str | None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        return await db.scalar(
            select(Product.youtube_id).where(
                Product.device_id == device_id, Product.night_id == night_id
            )
        )


async def _config(app: FastAPI, device_id: str) -> youtube.YouTubeSettings | None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        return youtube.settings_of(device)


def test_client_file() -> None:
    raw = json.dumps(CLIENT).encode()
    _, meta = stores.parse_youtube(raw, f"{BASE}/youtube/callback")
    assert meta == {"client_id": "123-abc.apps.googleusercontent.com", "project_id": "allskyhub"}
    with pytest.raises(stores.StoreError, match=r"Weiterleitungs-URI https://allskyhub\.org"):
        stores.parse_youtube(raw, "https://allskyhub.org/youtube/callback")
    installed = json.dumps({"installed": CLIENT["web"]}).encode()
    with pytest.raises(stores.StoreError, match="Webanwendung"):
        stores.parse_youtube(installed, f"{BASE}/youtube/callback")
    with pytest.raises(stores.StoreError, match="JSON"):
        stores.parse_youtube(b"nope", f"{BASE}/youtube/callback")


def test_titles() -> None:
    device = Device(name="Garten")
    night = youtube.title(device, "20261016", "timelapse")
    assert night == "Garten: die Nacht 16./17. Oktober 2026"
    assert (
        youtube.title(device, "20261031", "timelapse")
        == "Garten: die Nacht 31. Oktober/1. November 2026"
    )
    day = youtube.title(device, "20261016", "day_timelapse")
    assert day == "Garten: der Tag am 16. Oktober 2026"


async def test_uploads_each_recent_night_once(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, _, _ = await _setup(app, client)
    _vault(app)
    google = Google()
    service = _service(app, google)
    config = {"refresh_token": "1//refresh", "privacy": "unlisted", "since": "20261012"}
    await _set(app, device_id, youtube=config, public_slug="garten-abc")
    await _timelapse(app, device_id, "20261011", full=True)  # before connecting
    await _timelapse(app, device_id, "20261012", full=True)
    await _timelapse(app, device_id, "20261013", full=False)  # not on the hub yet

    now = dt.datetime(2026, 10, 14, 7, tzinfo=dt.UTC)
    await service.check(now)
    await service.check(now)
    assert await _video(app, device_id, "20261011") is None
    assert await _video(app, device_id, "20261012") == "dQw4w9WgXcQ"
    assert await _video(app, device_id, "20261013") is None
    assert google.uploaded == MP4
    sessions = [body for _, url, body in google.calls if url == youtube.UPLOAD]
    [meta] = [json.loads(body) for body in sessions]
    assert meta["snippet"]["title"] == "Garten: die Nacht 12./13. Oktober 2026"
    assert "http://testserver/sky/garten-abc/nights/20261012" in meta["snippet"]["description"]
    assert meta["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    stored = await _config(app, device_id)
    assert stored is not None
    assert stored.channel == "Sternwarte"

    # Two days later the night is history: never uploaded, even once it arrives.
    store: ImageStore = app.state.images
    path = store.product_path(device_id, "20261013", "timelapse.mp4", FrameVariant.FULL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MP4)
    await service.check(now + dt.timedelta(days=3))
    assert await _video(app, device_id, "20261013") is None


async def test_revoked_access_marks_broken(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, _, _ = await _setup(app, client)
    _vault(app)
    google = Google()
    google.refresh_error = "invalid_grant"
    service = _service(app, google)
    await _set(app, device_id, youtube={"refresh_token": "1//old", "since": "20261012"})
    await _timelapse(app, device_id, "20261012", full=True)
    await service.check(dt.datetime(2026, 10, 13, 7, tzinfo=dt.UTC))
    stored = await _config(app, device_id)
    assert stored is not None
    assert stored.broken
    assert await _video(app, device_id, "20261012") is None


async def test_connect_and_disconnect(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, _, _ = await _setup(app, client)
    vault: stores.Vault = app.state.stores.vault
    (vault.root / "youtube.secret").unlink(missing_ok=True)
    r = await client.get(f"/cameras/{device_id}")
    assert "Mit YouTube verbinden" not in r.text  # no OAuth client on this hub yet
    _vault(app)
    google = Google()
    _service(app, google)

    r = await client.post(
        f"/cameras/{device_id}/youtube/verbinden", data={"csrf_token": await home_csrf(client)}
    )
    assert r.status_code == 303
    target = urllib.parse.urlsplit(r.headers["location"])
    query: dict[str, Any] = dict(urllib.parse.parse_qsl(target.query))
    assert target.netloc == "accounts.google.com"
    assert query["scope"] == youtube.SCOPE
    assert query["redirect_uri"] == f"{BASE}/youtube/callback"

    r = await client.get("/youtube/callback", params={"state": "forged", "code": "x"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert await _config(app, device_id) is None

    r = await client.get("/youtube/callback", params={"state": query["state"], "code": "4/code"})
    assert r.status_code == 303
    assert r.headers["location"] == f"/cameras/{device_id}#youtube"
    stored = await _config(app, device_id)
    assert stored is not None
    assert stored.refresh_token == "1//refresh"  # noqa: S105 - the fake
    assert stored.privacy == "unlisted"

    r = await client.post(
        f"/cameras/{device_id}/youtube",
        data={"privacy": "public", "day": "on", "csrf_token": await home_csrf(client)},
    )
    stored = await _config(app, device_id)
    assert stored is not None
    assert (stored.privacy, stored.day) == ("public", True)
    page = await client.get(f"/cameras/{device_id}")
    assert "YouTube trennen" in page.text

    r = await client.post(
        f"/cameras/{device_id}/youtube/trennen", data={"csrf_token": await home_csrf(client)}
    )
    assert r.status_code == 303
    assert await _config(app, device_id) is None
    assert any(url == youtube.REVOKE for _, url, _ in google.calls)
