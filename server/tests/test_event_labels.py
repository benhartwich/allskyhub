"""The owner's verdict on detections ("kein Meteor") and the export for tuning."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import (
    Ack,
    Command,
    CommandName,
    ErrorReply,
    EventLabel,
    FrameVariant,
    Hello,
    NightDeleted,
    parse_envelope,
)
from allskyhub_server.devices import labels
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import EventRecord, NightStat
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, csrf_from, make_account, owner_email
from tests.test_web_events import EVENT, NIGHT, record
from tests.ws_helpers import device_ws, paired_token, send


async def test_labels_in_app_api_and_export(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(record(dev.device_id))
        await db.commit()
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    api = f"/api/v1/cameras/{dev.device_id}"
    label = f"{api}/events/{NIGHT}/{EVENT}/label"

    assert (await client.put(label, json={"label": "nonsense"}, headers=auth)).status_code == 400
    marked = await client.put(label, json={"label": "false_positive"}, headers=auth)
    assert marked.json()["label"] == "false_positive"
    assert [e["id"] for e in (await client.get(f"{api}/events", headers=auth)).json()] == [EVENT]
    hidden = await client.get(f"{api}/events", params={"hide_false": "true"}, headers=auth)
    assert hidden.json() == []

    export = (await client.get(f"{api}/events/export", headers=auth)).json()
    assert export[0]["id"] == EVENT
    assert export[0]["label"] == "false_positive"
    assert export[0]["labelled_at"] is not None
    assert export[0]["data"]["length_px"] == 412
    only = await client.get(f"{api}/events/export", params={"labelled_only": "true"}, headers=auth)
    assert len(only.json()) == 1

    cleared = await client.put(label, json={"label": None}, headers=auth)
    assert cleared.json()["label"] is None
    only = await client.get(f"{api}/events/export", params={"labelled_only": "true"}, headers=auth)
    assert only.json() == []


async def test_labels_in_web(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(record(dev.device_id))
        await db.commit()
    base = f"/cameras/{dev.device_id}"
    detail = f"{base}/events/{NIGHT}/{EVENT}"

    page = await client.get(detail)
    assert "Kein Meteor, sondern:" in page.text
    r = await client.post(
        f"{detail}/label",
        data={"label": "false_positive:aircraft", "csrf_token": csrf_from(page.text)},
    )
    assert r.status_code == 303
    assert "Als „kein Meteor“ markiert (Flugzeug)" in (await client.get(detail)).text
    # Hidden from the camera page and the list, unless asked for.
    assert f'href="{detail}"' not in (await client.get(base)).text
    assert f'href="{detail}"' not in (await client.get(f"{base}/events")).text
    assert "aussortiert" in (await client.get(f"{base}/events", params={"all": "true"})).text

    export = await client.get(f"{base}/events.json")
    assert export.headers["content-disposition"].startswith("attachment")
    assert export.json()[0]["label"] == "false_positive"

    await make_account(new_client)
    other = await new_client.get("/account")
    r = await new_client.post(
        f"{detail}/label", data={"label": "confirmed", "csrf_token": csrf_from(other.text)}
    )
    assert r.status_code == 404
    assert (await new_client.get(f"{base}/events.json")).status_code == 404


async def _setup(app: FastAPI, client: httpx.AsyncClient) -> tuple[FakeDevice, str, dict[str, str]]:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(record(dev.device_id))
        await db.commit()
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    return dev, token, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _event(app: FastAPI, device_id: str) -> EventRecord:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        event = await db.scalar(select(EventRecord).where(EventRecord.device_id == device_id))
    assert event is not None
    return event


async def test_label_goes_to_the_camera_until_acknowledged(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    """SPEC §6.4 Labels, §6.5 label_event: sent at once, again after every hello."""
    dev, token, auth = await _setup(app, client)
    url = f"/api/v1/cameras/{dev.device_id}/events/{NIGHT}/{EVENT}/label"
    r = await client.put(url, json={"label": "false_positive", "reason": "satellite"}, headers=auth)
    assert r.json()["label_reason"] == "satellite"
    bad = await client.put(url, json={"label": "confirmed", "reason": "aircraft"}, headers=auth)
    assert bad.status_code == 400  # a reason only with false_positive
    assert (await _event(app, dev.device_id)).label_pending  # the camera was offline

    async with device_ws(live_app, token) as ws:
        await send(ws, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="1"))
        env = parse_envelope(await asyncio.wait_for(ws.recv(), 5))
        assert isinstance(env.body, Command)
        assert env.body.name is CommandName.LABEL_EVENT
        args = EventLabel.model_validate(env.body.args)
        assert (args.event_id, args.label, args.reason) == (EVENT, "false_positive", "satellite")
        await send(ws, ErrorReply(ref=env.id, code="unsupported"))
        for _ in range(100):
            if not (await _event(app, dev.device_id)).label_pending:
                break
            await asyncio.sleep(0.05)
    event = await _event(app, dev.device_id)
    assert not event.label_pending  # an older camera: the hub keeps the label for itself
    assert event.label == "false_positive"

    # A new label is sent at once while the camera is online; its ack ends it.
    async with device_ws(live_app, token) as ws:
        await send(ws, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="1"))
        await asyncio.sleep(0.2)
        await client.put(url, json={"label": None}, headers=auth)
        env = parse_envelope(await asyncio.wait_for(ws.recv(), 5))
        assert isinstance(env.body, Command)
        assert EventLabel.model_validate(env.body.args).label is None
        await send(ws, Ack(ref=env.id))
        for _ in range(100):
            if not (await _event(app, dev.device_id)).label_pending:
                break
            await asyncio.sleep(0.05)
    assert not (await _event(app, dev.device_id)).label_pending


async def test_newer_label_wins(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev, _, auth = await _setup(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    old = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    async with maker() as db:
        body = EventLabel(night_id=NIGHT, event_id=EVENT, label="false_positive",
                          reason="aircraft", at=old)  # fmt: skip
        assert await labels.from_device(db, dev.device_id, body)
    event = await _event(app, dev.device_id)
    assert (event.label, event.label_reason, event.label_pending) == (
        "false_positive",
        "aircraft",
        False,
    )
    url = f"/api/v1/cameras/{dev.device_id}/events/{NIGHT}/{EVENT}/label"
    await client.put(url, json={"label": "confirmed"}, headers=auth)  # now: newer
    async with maker() as db:
        stale = EventLabel(night_id=NIGHT, event_id=EVENT, label=None, at=old)
        assert not await labels.from_device(db, dev.device_id, stale)
    assert (await _event(app, dev.device_id)).label == "confirmed"
    # The night's numbers follow the label (a false positive counts nowhere).
    async with maker() as db:
        stat = await db.get(NightStat, (dev.device_id, NIGHT))
    assert stat is not None
    assert stat.meteors == 1
    await client.put(url, json={"label": "false_positive", "reason": "other"}, headers=auth)
    async with maker() as db:
        stat = await db.get(NightStat, (dev.device_id, NIGHT))
    assert stat is not None
    assert stat.meteors == 0
    csv = await client.get(f"/api/v1/cameras/{dev.device_id}/exports/meteors.csv", headers=auth)
    assert EVENT not in csv.text


async def test_night_deleted(app: FastAPI, client: httpx.AsyncClient, live_app: str) -> None:
    """SPEC §6.3 night_deleted: frames, products and events of that night go."""
    dev, token, _ = await _setup(app, client)
    store: ImageStore = app.state.images
    picture = store.event_path(dev.device_id, NIGHT, EVENT, FrameVariant.THUMB)
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(b"x")
    async with device_ws(live_app, token) as ws:
        await send(ws, NightDeleted(night_id=NIGHT))
        await asyncio.sleep(0.3)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        left = await db.scalar(
            select(func.count())
            .select_from(EventRecord)
            .where(EventRecord.device_id == dev.device_id)
        )
    assert left == 0
    assert not (store.root / dev.device_id / NIGHT).exists()
