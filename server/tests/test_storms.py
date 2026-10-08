"""Thunderstorms: lightning flashes grouped by data.storm (SPEC §6.4)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import events
from allskyhub_server.devices.event_text import event_rows
from allskyhub_server.models import EventRecord, PushToken, User
from allskyhub_server.push.notify import Notifier
from tests.fake_device import FakeDevice
from tests.helpers import make_account
from tests.test_push import FakeSender
from tests.test_web_events import record
from tests.ws_helpers import paired_token

NIGHT = "20261008"
STORM = "storm-20261008T213012Z"
T0 = dt.datetime(2026, 10, 8, 21, 30, 12, tzinfo=dt.UTC)


def flash(device_id: str, minute: int, area: float, storm: str = STORM) -> EventRecord:
    start = T0 + dt.timedelta(minutes=minute)
    return EventRecord(
        device_id=device_id, event_id=f"lightning-20261008T21{30 + minute:02d}12Z",
        night_id=NIGHT, kind="lightning", start=start, end=start, confidence=0.9,
        has_image=True, has_thumb=True,
        data={"area_frac": area, "peak": 0.4, "storm_flashes": minute + 1, "storm": storm},
    )  # fmt: skip


def test_collapse_and_rows() -> None:
    rows = [flash("d", 20, 0.1), record("d"), flash("d", 10, 0.6), flash("d", 0, 0.3)]
    rows[1].start = T0 + dt.timedelta(minutes=15)
    items = events.collapse(rows)
    assert len(items) == 2
    storm = items[0]
    assert isinstance(storm, events.Storm)
    assert len(storm.flashes) == 3
    assert storm.cover.data["area_frac"] == 0.6
    assert (storm.start, storm.end) == (T0, T0 + dt.timedelta(minutes=20))
    labels = dict(event_rows(flash("d", 0, 0.25)))
    assert labels["Erhellter Himmel"] == "25 %"
    assert labels["Blitze in 30 Min."] == "1"
    assert "storm" not in labels
    # A malformed storm key is not grouped.
    odd = flash("d", 1, 0.1, storm="nope")
    assert events.collapse([odd]) == [odd]


async def test_storm_in_web(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add_all([flash(dev.device_id, m, a) for m, a in ((0, 0.3), (10, 0.6), (20, 0.1))])
        db.add(record(dev.device_id))
        await db.commit()
    base = f"/cameras/{dev.device_id}"
    storm_link = f'href="{base}/storms/{STORM}"'
    for page in (base, f"{base}/events", f"{base}/nights/{NIGHT}"):
        text = (await client.get(page)).text
        assert storm_link in text
        assert "Gewitter · 3 Blitze" in text
        assert text.count("lightning-2026") <= 1  # the cover thumbnail only
    storm = (await client.get(f"{base}/storms/{STORM}")).text
    assert storm.count('class="event-thumb"') == 3
    assert (await client.get(f"{base}/storms/storm-20261001T000000Z")).status_code == 404
    await make_account(new_client)
    assert (await new_client.get(f"{base}/storms/{STORM}")).status_code == 404


async def test_push_once_per_storm(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    sender = FakeSender()
    notifier = Notifier(app.state.sessionmaker, sender)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        rows = [flash(dev.device_id, m, 0.2) for m in (0, 1, 2)]
        rows.append(flash(dev.device_id, 50, 0.2, storm="storm-20261008T222012Z"))
        db.add_all(rows)
        user_id = (
            await db.execute(update(User).values(notify_events=True).returning(User.id))
        ).scalar_one()
        db.add(PushToken(user_id=user_id, token="fcm-token-storm", platform="android"))  # noqa: S106
        await db.commit()
    for row in rows:
        await notifier.event(dev.device_id, row.event_id)
    titles = [m.title for _, m in sender.sent]
    assert len(titles) == 2  # one per storm, not one per flash or per 10 minutes
    assert all(t.startswith("Gewitter über") for t in titles)
