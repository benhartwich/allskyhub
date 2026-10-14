"""Aurora alarm (roadmap C2) and the summary of a night (C3)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import night_summary
from allskyhub_server.models import Device, EventRecord, PushToken, SkySample, User
from allskyhub_server.push.notify import Notifier
from tests.fake_device import FakeDevice
from tests.test_push import FakeSender
from tests.ws_helpers import paired_token

NIGHT = "20261012"
EVENING = dt.datetime(2026, 10, 12, 19, tzinfo=dt.UTC)


async def _setup(
    app: FastAPI, client: httpx.AsyncClient, *, lat: float = 48.14, **user: bool
) -> tuple[str, FakeSender, Notifier]:
    dev = FakeDevice()
    await paired_token(client, dev)
    sender = FakeSender()
    notifier = Notifier(app.state.sessionmaker, sender)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = {
            "settings": {"latitude": lat, "longitude": 14.39, "timezone": "Europe/Vienna"},
            "sky": {"at": dt.datetime.now(dt.UTC).isoformat(), "night_id": NIGHT,
                    "cloud_cover": 0.1, "sqm_mag": 21.0, "stars": 400},
        }  # fmt: skip
        device.last_frame = {"mode": "night"}
        if user:
            await db.execute(update(User).where(User.id == device.owner_id).values(**user))
        token = f"fcm-token-{dev.device_id}"  # a test value
        db.add(PushToken(user_id=device.owner_id, token=token, platform="android"))
        await db.commit()
    return dev.device_id, sender, notifier


async def _set(app: FastAPI, device_id: str, **values: object) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(update(Device).where(Device.id == device_id).values(**values))
        await db.commit()


async def test_aurora_alarm_needs_kp_clear_sky_and_dark(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """The test Kp is 4.67: enough at 65° N (rule of thumb 0), not at 48° N (about 8)."""
    device_id, sender, notifier = await _setup(app, client, lat=65.0, notify_aurora=True)
    await notifier.aurora(device_id, app.state.kp)
    assert [m.title for _, m in sender.sent] == ["Polarlicht möglich über Garten"]
    assert "Kp 4,7" in sender.sent[0][1].body
    await notifier.aurora(device_id, app.state.kp)
    assert len(sender.sent) == 1  # quiet for 6 hours

    await _set(app, device_id, aurora_notified_at=None, last_frame={"mode": "day"})
    await notifier.aurora(device_id, app.state.kp)
    assert len(sender.sent) == 1  # not at day

    low_id, low_sender, low_notifier = await _setup(app, client, notify_aurora=True)
    await low_notifier.aurora(low_id, app.state.kp)
    assert low_sender.sent == []  # 48° N needs about Kp 8


async def test_aurora_alarm_off_cloudy_or_stale(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, sender, notifier = await _setup(app, client, lat=65.0)
    await notifier.aurora(device_id, app.state.kp)
    assert sender.sent == []  # not switched on
    device_id, sender, notifier = await _setup(app, client, lat=65.0, notify_aurora=True)
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)).isoformat()
    for sky in ({"at": dt.datetime.now(dt.UTC).isoformat(), "cloud_cover": 0.8},
                {"at": old, "cloud_cover": 0.1}):  # fmt: skip
        status = {"settings": {"latitude": 65.0}, "sky": sky}
        await _set(app, device_id, last_status=status)
        await notifier.aurora(device_id, app.state.kp)
    assert sender.sent == []


async def _night(app: FastAPI, device_id: str) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        for i, (kind, data, conf) in enumerate(
            [
                ("meteor", {"shower": "Orionids", "shower_match": "radiant"}, 0.8),
                ("meteor", {"shower": None, "shower_match": "radiant"}, 0.7),
                ("aurora", {}, 0.6),
                ("lightning", {"storm": "storm-20261012T200000Z"}, 0.9),
                ("lightning", {"storm": "storm-20261012T200000Z"}, 0.9),
            ]
        ):
            t = EVENING + dt.timedelta(hours=1, minutes=i)
            db.add(EventRecord(device_id=device_id, event_id=f"{kind}-{i}", night_id=NIGHT,
                               kind=kind, start=t, end=t, confidence=conf, has_image=True,
                               has_thumb=True, data=data))  # fmt: skip
        for i in range(19):  # every 10 minutes for 3 hours, clear for the first 2 hours
            db.add(SkySample(device_id=device_id, night_id=NIGHT,
                             at=EVENING + dt.timedelta(minutes=10 * i),
                             cloud_cover=0.1 if i < 12 else 0.9, sqm_mag=20.0 + i * 0.05,
                             stars=100))  # fmt: skip
        await db.commit()


async def test_summary_of_a_night(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, _, _ = await _setup(app, client)
    await _night(app, device_id)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        s = await night_summary.summarize(db, device_id, NIGHT)
    assert s.best is not None
    assert s.best.kind == "aurora"  # rarer than meteors
    assert s.line() == (
        "2 Meteore: 1 Orioniden, 1 sporadisch, 1 Polarlicht, 1 Gewitter, 2,0 h klar, "
        "beste Himmelshelligkeit 20,90 mag/″²"
    )
    async with maker() as db:
        empty = await night_summary.summarize(db, device_id, "20261001")
    assert empty.empty
    assert (
        night_summary.last_night_id(
            dt.datetime(2026, 10, 13, 6, tzinfo=dt.UTC), dt.timezone(dt.timedelta(hours=2))
        )
        == NIGHT
    )


async def test_morning_summary_once_after_sunrise(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, sender, notifier = await _setup(app, client, notify_summary=True)
    await _night(app, device_id)
    before = dt.datetime(2026, 10, 13, 4, tzinfo=dt.UTC)  # 06:00 local, before sunrise
    await notifier.check_morning(before)
    assert sender.sent == []
    morning = dt.datetime(2026, 10, 13, 6, tzinfo=dt.UTC)  # 08:00 local
    await notifier.check_morning(morning)
    await notifier.check_morning(morning + dt.timedelta(minutes=5))
    assert [m.title for _, m in sender.sent] == ["Die Nacht über Garten"]
    message = sender.sent[0][1]
    assert message.body.startswith("2 Meteore")
    assert message.data == {"camera": device_id, "night": NIGHT, "event": "aurora-2"}


async def test_camera_page_shows_last_night(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, _, _ = await _setup(app, client)
    yesterday = night_summary.last_night_id(
        dt.datetime.now(dt.UTC), dt.timezone(dt.timedelta(hours=1))
    )
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(SkySample(device_id=device_id, night_id=yesterday, at=dt.datetime.now(dt.UTC),
                         cloud_cover=0.1, sqm_mag=21.3, stars=100))  # fmt: skip
        await db.commit()
    page = (await client.get(f"/cameras/{device_id}")).text
    assert "Letzte Nacht" in page
    assert "21,30 mag/″²" in page


async def test_aurora_network_alerts_clear_cameras_around(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """Roadmap N2: a network camera's own aurora event, not a forecast, alerts cameras
    around it with a clear, dark sky, once."""
    source, _, notifier = await _setup(app, client, lat=56.0)
    clear, _, _ = await _setup(app, client, lat=52.0, notify_aurora=True)
    cloudy, _, _ = await _setup(app, client, lat=53.0, notify_aurora=True)
    await _setup(app, client, lat=59.0, notify_aurora=True)  # too far poleward
    far, _, _ = await _setup(app, client, lat=50.0, notify_aurora=True)
    await _set(app, far, last_status={
        "settings": {"latitude": 50.0, "longitude": 40.0},
        "sky": {"at": dt.datetime.now(dt.UTC).isoformat(), "cloud_cover": 0.0}})  # fmt: skip
    await _set(app, cloudy, last_status={
        "settings": {"latitude": 53.0, "longitude": 14.39},
        "sky": {"at": dt.datetime.now(dt.UTC).isoformat(), "cloud_cover": 0.9}})  # fmt: skip
    now = dt.datetime.now(dt.UTC)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        data = {"peak_index": 12, "green": 140, "azimuth_deg": 355.0, "altitude_deg": 12.0,
                "ongoing": True}  # fmt: skip
        db.add(EventRecord(device_id=source, event_id="aurora-1", night_id=NIGHT, kind="aurora",
                           start=now - dt.timedelta(minutes=10), end=now, confidence=0.8,
                           has_image=False, data=data))  # fmt: skip
        await db.commit()

    sender = FakeSender()
    notifier = Notifier(maker, sender)
    await notifier.aurora_seen(source, "aurora-1")
    assert sender.sent == []  # the source camera is not in the network

    await _set(app, source, network=True)
    await notifier.aurora_seen(source, "aurora-1")
    assert [m.data["camera"] for _, m in sender.sent] == [clear]
    message = sender.sent[0][1]
    assert message.title == "Polarlicht gesichtet, Himmel klar über Garten"
    assert "etwa 445 km nördlich" in message.body
    assert "Blick tief nach Norden" in message.body
    await notifier.aurora_seen(source, "aurora-1")  # the event grows: no second alarm
    assert len(sender.sent) == 1
