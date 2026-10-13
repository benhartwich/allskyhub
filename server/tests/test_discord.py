"""Detections and the night in a Discord channel (roadmap H5)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices import discord
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, EventRecord
from tests.helpers import home_csrf
from tests.test_alarms import _night, _set, _setup  # pyright: ignore[reportPrivateUsage]
from tests.test_app_manage import _camera  # pyright: ignore[reportPrivateUsage]

HOOK = "https://discord.com/api/webhooks/123456789012/" + "a" * 68
JPEG = b"\xff\xd8\xff\xe0 picture"


class FakePoster(discord.Poster):
    def __init__(self, answer: Literal["ok", "gone", "failed"] = "ok") -> None:
        super().__init__()
        self.answer: Literal["ok", "gone", "failed"] = answer
        self.posts: list[tuple[str, str, bytes | None]] = []

    async def post(
        self, webhook: str, text: str, picture: bytes | None = None, name: str = "bild.jpg"
    ) -> Literal["ok", "gone", "failed"]:
        self.posts.append((webhook, text, picture))
        return self.answer


def test_only_discord_webhooks() -> None:
    assert discord.DiscordSettings(webhook=f" {HOOK} ").webhook == HOOK
    for bad in (
        "http://discord.com/api/webhooks/123456789012/" + "a" * 68,
        "https://example.org/api/webhooks/123456789012/" + "a" * 68,
        "https://discord.com.evil.org/api/webhooks/123456789012/" + "a" * 68,
        HOOK + "/../../x",
    ):
        with pytest.raises(ValueError, match="Discord"):
            discord.DiscordSettings(webhook=bad)
    assert discord.masked(HOOK) == "https://discord.com/api/webhooks/123456789012/…"


async def _add_event(
    app: FastAPI, device_id: str, event_id: str, kind: str, *, has_image: bool, **data: object
) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    now = dt.datetime.now(dt.UTC)
    async with maker() as db:
        db.add(EventRecord(device_id=device_id, event_id=event_id, night_id="20261012",
                           kind=kind, start=now, end=now, confidence=0.9,
                           has_image=has_image, data=dict(data)))  # fmt: skip
        await db.commit()


async def test_events_go_out_once_with_their_picture(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    device_id, _, _ = await _setup(app, client)
    store: ImageStore = app.state.images
    poster = FakePoster()
    poster_app = discord.Discord(app.state.sessionmaker, store, poster, "https://hub.example")
    await _set(app, device_id, discord={"webhook": HOOK, "kinds": ["meteor", "lightning"]})

    await _add_event(app, device_id, "m1", "meteor", has_image=True)
    await poster_app.event(device_id, "m1", picture_arrived=False)
    assert poster.posts == []  # waits for the picture
    store.save_event(device_id, "20261012", "m1", FrameVariant.THUMB, JPEG)
    await poster_app.event(device_id, "m1", picture_arrived=True)
    await poster_app.event(device_id, "m1", picture_arrived=True)
    [(hook, text, picture)] = poster.posts
    assert hook == HOOK
    assert text.startswith("**Meteor über Garten** um ")
    assert picture == JPEG

    await _add_event(app, device_id, "a1", "aurora", has_image=False)
    await poster_app.event(device_id, "a1", picture_arrived=False)
    assert len(poster.posts) == 1  # aurora not chosen

    storm = "storm-20261012T200000Z"
    for i in range(3):
        await _add_event(app, device_id, f"l{i}", "lightning", has_image=False, storm=storm)
        await poster_app.event(device_id, f"l{i}", picture_arrived=False)
    assert [t.split(" um ")[0] for _, t, _ in poster.posts[1:]] == ["**Gewitter über Garten**"]

    # Deleted in Discord: marked, and nothing more is tried.
    poster.answer = "gone"
    poster_app._last.clear()  # pyright: ignore[reportPrivateUsage]  # past the quiet time
    await _set(app, device_id, discord={"webhook": HOOK, "kinds": ["aurora"]})
    await _add_event(app, device_id, "a2", "aurora", has_image=False)
    await poster_app.event(device_id, "a2", picture_arrived=False)
    await _add_event(app, device_id, "a3", "aurora", has_image=False)
    await poster_app.event(device_id, "a3", picture_arrived=False)
    assert len(poster.posts) == 3
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        config = discord.settings_of(device)
        assert config is not None
        assert config.broken


async def test_morning_summary_once(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, _, _ = await _setup(app, client)
    await _night(app, device_id)
    store: ImageStore = app.state.images
    path = store.product_path(device_id, "20261012", "keogram.jpg", FrameVariant.THUMB)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(JPEG)
    poster = FakePoster()
    poster_app = discord.Discord(app.state.sessionmaker, store, poster, "https://hub.example")
    await _set(app, device_id, discord={"webhook": HOOK}, public_slug="garten-abc")
    await poster_app.check_morning(dt.datetime(2026, 10, 13, 4, tzinfo=dt.UTC))
    assert poster.posts == []  # before sunrise
    morning = dt.datetime(2026, 10, 13, 6, tzinfo=dt.UTC)
    await poster_app.check_morning(morning)
    await poster_app.check_morning(morning + dt.timedelta(minutes=5))
    [(_, text, picture)] = poster.posts
    assert text.startswith("**Die Nacht über Garten**\n2 Meteore")
    assert text.endswith("https://hub.example/sky/garten-abc/nights/20261012")
    assert picture == JPEG


async def test_web_form(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, _ = await _camera(client)
    base = f"/cameras/{device_id}/discord"
    r = await client.post(
        base, data={"webhook": "https://example.org/x", "csrf_token": await home_csrf(client)}
    )
    assert r.status_code == 400
    assert "keine Discord-Webhook-Adresse" in r.text
    data = {"webhook": HOOK, "kind_meteor": "on", "csrf_token": await home_csrf(client)}
    assert (await client.post(base, data=data)).status_code == 303
    page = (await client.get(f"/cameras/{device_id}")).text
    assert "https://discord.com/api/webhooks/123456789012/…" in page
    assert "a" * 68 not in page  # the secret part is never shown
    # An empty address keeps the stored one.
    data = {"webhook": "", "kind_aurora": "on", "summary": "on",
            "csrf_token": await home_csrf(client)}  # fmt: skip
    assert (await client.post(base, data=data)).status_code == 303
    r = await client.post(f"{base}/remove", data={"csrf_token": await home_csrf(client)})
    assert r.status_code == 303
    assert "123456789012/…" not in (await client.get(f"/cameras/{device_id}")).text


async def test_app_api(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(client)
    url = f"/api/v1/cameras/{device_id}/discord"
    state = (await client.get(url, headers=auth)).json()
    assert state["configured"] is False
    assert state["available"]["meteor"] == "Meteore"
    r = await client.put(url, json={"webhook": "https://example.org"}, headers=auth)
    assert r.status_code == 400
    r = await client.put(url, json={"webhook": HOOK, "kinds": ["meteor", "x"]}, headers=auth)
    assert r.json()["webhook"].endswith("/123456789012/…")
    assert r.json()["kinds"] == ["meteor"]
    r = await client.put(url, json={"kinds": ["aurora"], "summary": False}, headers=auth)
    assert r.json()["configured"] is True
    assert (r.json()["kinds"], r.json()["summary"]) == (["aurora"], False)

    poster = FakePoster()
    app.state.notifier.discord._poster = poster  # pyright: ignore[reportPrivateUsage]
    assert (await client.post(f"{url}/test", headers=auth)).status_code == 204
    assert poster.posts[0][1].startswith("allskyhub ist verbunden")
    poster.answer = "gone"
    assert (await client.post(f"{url}/test", headers=auth)).status_code == 409

    assert (await client.delete(url, headers=auth)).json()["configured"] is False
