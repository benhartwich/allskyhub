"""Fireballs with a possible fall area: geometry, the alarm and the public pages."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import fireball, meteor_pairs
from allskyhub_server.devices import triangulation as t
from allskyhub_server.models import Device, EventRecord, MeteorPair, PushToken, User
from allskyhub_server.push.notify import Notifier
from tests.fake_device import FakeDevice
from tests.helpers import claim, make_account
from tests.test_push import FakeSender

LINZ = (48.30, 14.29)
SALZBURG = (47.80, 13.04)
NEARBY = (48.20, 13.60)
HAMBURG = (53.55, 10.0)
# Glows from 90 km down to 25 km, flying south-east.
TOP, BOTTOM = (48.05, 13.70, 90.0), (47.90, 13.95, 25.0)


def _pair(**values: float) -> MeteorPair:
    base = {"high_lat": TOP[0], "high_lon": TOP[1], "high_km": TOP[2],
            "low_lat": BOTTOM[0], "low_lon": BOTTOM[1], "low_km": BOTTOM[2],
            "length_km": 70.0, "convergence_deg": 40.0}  # fmt: skip
    return MeteorPair(**(base | values))


def test_fall_area_lies_ahead_of_the_end() -> None:
    area = fireball.fall_area(_pair())
    assert area is not None
    assert fireball.heading(area.azimuth_deg) == "Südosten"
    near = fireball.distance_km(BOTTOM[0], BOTTOM[1], *area.near)
    far = fireball.distance_km(BOTTOM[0], BOTTOM[1], *area.far)
    assert 0 < near < far <= fireball.MAX_DARK_FLIGHT_KM
    # Ahead, not behind: farther from where the glow began.
    assert fireball.distance_km(TOP[0], TOP[1], *area.far) > fireball.distance_km(
        TOP[0], TOP[1], BOTTOM[0], BOTTOM[1]
    )
    outline = area.outline()
    assert outline[0] == outline[-1]
    assert len(outline) == 5
    assert area.half_width_km == pytest.approx(4.5)


def test_high_end_has_no_fall_area() -> None:
    pair = _pair(low_km=60.0)
    assert fireball.fall_area(pair) is None
    assert not fireball.is_fireball(pair)
    assert fireball.is_fireball(_pair(low_km=60.0, peak=0.95))


def test_steep_fall_lands_close_to_the_end() -> None:
    steep = _pair(high_lat=47.91, high_lon=13.95)
    area = fireball.fall_area(steep)
    assert area is not None
    assert fireball.distance_km(BOTTOM[0], BOTTOM[1], *area.far) < 3


async def test_alarm_and_pages(app: FastAPI, client: httpx.AsyncClient) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    start = dt.datetime.now(dt.UTC).replace(microsecond=0) - dt.timedelta(minutes=5)
    top, bottom = t.ecef(*TOP), t.ecef(*BOTTOM)
    transport = httpx.ASGITransport(app=app)

    async def camera(place: tuple[float, float], second: int, meteor: bool) -> str:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            await make_account(c)
            dev = FakeDevice()
            reg = await dev.register(c)
            assert reg.pairing_code
            await claim(c, reg.pairing_code, f"Kamera {place[0]}")
        async with maker() as db:
            device = await db.get(Device, dev.device_id)
            assert device
            device.network = True
            device.last_status = {"settings": {"latitude": place[0], "longitude": place[1]}}
            await db.execute(
                update(User).where(User.id == device.owner_id).values(notify_fireball=True)
            )
            db.add(PushToken(user_id=device.owner_id, token=f"t-{place}", platform="ios"))
            if meteor:
                az1, alt1 = t.look(place[0], place[1], 0.0, top)
                az2, alt2 = t.look(place[0], place[1], 0.0, bottom)
                begin = start + dt.timedelta(seconds=second)
                db.add(EventRecord(device_id=dev.device_id, event_id=f"meteor-{second}",
                                   night_id="20261013", kind="meteor", start=begin,
                                   end=begin + dt.timedelta(seconds=30), confidence=0.9,
                                   has_image=False, data={"start_azimuth_deg": az1,
                                   "start_altitude_deg": alt1, "end_azimuth_deg": az2,
                                   "end_altitude_deg": alt2, "track_ordered": True,
                                   "peak": 1.0}))  # fmt: skip
            await db.commit()
        return dev.device_id

    linz = await camera(LINZ, 0, True)
    salzburg = await camera(SALZBURG, 10, True)
    await camera(NEARBY, 0, False)
    await camera(HAMBURG, 0, False)

    sender = FakeSender()
    notifier = Notifier(maker, sender)
    assert await meteor_pairs.pair_event(maker, linz, "meteor-0", notifier.fireball) == 1
    # The same meteor again (an updated event): no second alarm.
    assert await meteor_pairs.pair_event(maker, salzburg, "meteor-10", notifier.fireball) == 1
    tokens = sorted(tokens[0] for tokens, _ in sender.sent)
    assert tokens == sorted(f"t-{p}" for p in (LINZ, SALZBURG, NEARBY))
    message = sender.sent[0][1]
    assert message.title == "Feuerkugel: Meteoritenfall möglich"
    assert "in 25 km Höhe" in message.body
    pair_id = message.data["fireball"]

    # Both cameras are in the network: public, without the cameras' places.
    listing = (await client.get("/feuerkugeln")).text
    assert f"/feuerkugeln/{pair_id}" in listing
    assert "Meteoritenfall möglich" in listing
    page = await client.get(f"/feuerkugeln/{pair_id}")
    assert page.status_code == 200
    assert "Mögliches Fallgebiet" in page.text
    assert "eine Kamera im Kameranetz" in page.text
    assert "48.3" not in page.text.split('id="map-config"')[0]

    # One owner leaves: no longer public.
    async with maker() as db:
        await db.execute(update(Device).where(Device.id == linz).values(network=False))
        await db.commit()
    assert (await client.get(f"/feuerkugeln/{pair_id}")).status_code == 404
    assert f"/feuerkugeln/{pair_id}" not in (await client.get("/feuerkugeln")).text
