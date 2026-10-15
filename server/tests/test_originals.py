"""Original frames and overflights (SPEC §6.4 Original frame and Overflights, §6.5;
roadmap L28, L29)."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection

from allskyhub_protocol import CommandName, Event, EventKind, EventVariant, event_id
from allskyhub_server import maintenance
from allskyhub_server.devices import events
from allskyhub_server.devices.images import ImageStore, jpeg_size
from allskyhub_server.devices.night_summary import summarize
from allskyhub_server.models import EventRecord
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.ws_helpers import JPEG, device_ws, next_command, paired_token, send

NIGHT = "20261014"
START = dt.datetime(2026, 10, 14, 21, 0, tzinfo=dt.UTC)


def _jpeg(width: int, height: int) -> bytes:
    """A JPEG with an APP0 segment and a baseline frame header."""
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0\x00\x11\x08" + height.to_bytes(2) + width.to_bytes(2) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def test_jpeg_size() -> None:
    assert jpeg_size(_jpeg(3096, 2080)) == (3096, 2080)
    assert jpeg_size(JPEG) is None
    assert jpeg_size(b"\xff\xd8") is None


def _event(minutes: int, data: dict[str, Any], kind: EventKind = EventKind.METEOR) -> Event:
    at = START + dt.timedelta(minutes=minutes)
    return Event(
        id=event_id(kind, at), night_id=NIGHT, kind=kind, start=at,
        end=at + dt.timedelta(seconds=30), confidence=0.8, has_image=True,
        data={"length_px": 300, "peak": 0.5, "frames": 2, **data},
    )  # fmt: skip


async def _put(host: str, token: str, ev: Event, variant: EventVariant, body: bytes) -> int:
    async with httpx.AsyncClient(base_url=f"http://{host}") as dc:
        r = await dc.put(
            f"/device/v1/events/{NIGHT}/{ev.id}",
            params={"variant": variant.value},
            content=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code


async def _requests(ws: ClientConnection, first: float = 3.0) -> list[tuple[str, EventVariant]]:
    """Every upload_event the hub sends until it is quiet: up to ``first`` seconds for the
    first command (slow CI runners), then 0.5 s of silence ends it."""
    out: list[tuple[str, EventVariant]] = []
    wait = first
    while True:
        try:
            cmd = await asyncio.wait_for(next_command(ws), wait)
        except TimeoutError:
            return out
        wait = 0.5
        if cmd.name is CommandName.UPLOAD_EVENT:
            out.append((cmd.args["event_id"], EventVariant(cmd.args["variant"])))


FRAME = {"frame": "image-20261014210000.jpg", "crop_x": 100, "crop_y": 50, "crop_w": 400,
         "crop_h": 300}  # fmt: skip


async def test_originals_come_with_real_events_and_on_demand(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    real = _event(0, FRAME)
    moving = _event(5, {**FRAME, "trail": "moving"})
    old_agent = _event(10, {})
    async with device_ws(live_app, token) as ws:
        for ev in (real, moving, old_agent):
            await send(ws, ev)
        asked = await _requests(ws)
        # Thumbnails for all; the original only for the event that is not a false positive.
        assert (real.id, EventVariant.ORIGINAL) in asked
        assert (moving.id, EventVariant.ORIGINAL) not in asked
        assert (old_agent.id, EventVariant.ORIGINAL) not in asked
        assert await _put(live_app, token, real, EventVariant.ORIGINAL, _jpeg(3096, 2080)) == 204

        base = f"/cameras/{dev.device_id}/events/{NIGHT}"
        page = await client.get(f"{base}/{real.id}")
        assert 'viewBox="0 0 3096 2080"' in page.text
        assert 'x="100" y="50" width="400" height="300"' in page.text
        original = await client.get(f"{base}/{real.id}/image", params={"variant": "original"})
        assert original.status_code == 200

        # A false positive: one click asks the camera for it.
        page = await client.get(f"{base}/{moving.id}")
        assert "Ganzes Bild zeigen" in page.text
        fetching = await client.get(f"{base}/{moving.id}/original")
        assert "wird von der Kamera geholt" in fetching.text
        asked = await _requests(ws)
        assert [a for a in asked if a[1] is EventVariant.ORIGINAL] == [
            (moving.id, EventVariant.ORIGINAL)
        ]
        # It never comes (gone on the camera): the page gives up after a while.
        gone = await client.get(f"{base}/{moving.id}/original", params={"n": 20})
        assert "nicht geschickt" in gone.text

        # Events without a named frame have no original.
        assert "Ganzes Bild" not in (await client.get(f"{base}/{old_agent.id}")).text
        missing = await client.get(f"{base}/{old_agent.id}/original")
        assert "Ganzes Bild" not in missing.text
        assert (
            await client.get(f"{base}/{old_agent.id}/image", params={"variant": "original"})
        ).status_code == 404

    # Kept like the full picture: gone after 7 days for the default event retention.
    store: ImageStore = app.state.images
    path = store.event_path(dev.device_id, NIGHT, real.id, EventVariant.ORIGINAL)
    assert path.exists()
    async with app.state.sessionmaker() as db:
        await maintenance.purge(
            db, store, settings, now=dt.datetime.now(dt.UTC) + dt.timedelta(days=8)
        )
    assert not path.exists()


def _record(**kw: Any) -> EventRecord:
    base: dict[str, Any] = {
        "kind": "meteor", "data": {}, "label": None, "label_reason": None,
        "label_detail": None, "has_image": True, "original_w": None, "original_h": None,
    }  # fmt: skip
    base.update(kw)
    return EventRecord(**base)


def test_overflight_groups_and_names() -> None:
    sat = _record(
        data={"trail": "moving", "start_azimuth_deg": 315.0, "start_altitude_deg": 14.6,
              "end_azimuth_deg": 140.0, "end_altitude_deg": 31.0},
        label="false_positive", label_reason="satellite", label_detail="STARLINK-1234 44238",
    )  # fmt: skip
    plane = _record(data={"trail": "dashed"}, label="false_positive", label_reason="aircraft",
                    label_detail="blinkt")  # fmt: skip
    unknown = _record(data={"trail": "circle"}, label="false_positive", label_reason="other",
                      label_detail="bewegtes Objekt")  # fmt: skip
    meteor = _record(data={"trail": "meteor"})
    confirmed = _record(data={"trail": "moving"}, label="confirmed")
    star = _record(data={"trail": "star"}, label="false_positive", label_reason="artifact")
    assert [events.overflight(e) for e in (sat, plane, unknown, meteor, confirmed, star)] == [
        "satellite", "aircraft", "unknown", None, None, None,
    ]  # fmt: skip
    assert events.overflight_name(sat) == "STARLINK-1234 44238"
    assert events.overflight_name(plane) is None  # the camera's verdict, not a name
    assert events.overflight_path(sat) == "NW 15° → SO 31°"
    assert events.overflight_path(plane) is None
    assert (
        events.overflight_count([sat, plane, unknown, unknown, meteor])
        == "4 Überflüge: 1 Satellit, 1 Flugzeug, 2 unbekannt"
    )
    assert events.overflight_count([meteor]) is None


def test_crop_is_checked_against_the_frame() -> None:
    assert events.crop(_record(data=FRAME, original_w=3096, original_h=2080)) == (
        100, 50, 400, 300,
    )  # fmt: skip
    assert events.crop(_record(data=FRAME, original_w=300, original_h=200)) is None
    lightning = {"frame": "x.jpg", "crop_x": None, "crop_y": None, "crop_w": None, "crop_h": None}
    assert events.crop(_record(data=lightning)) is None


async def test_overflight_page_and_summary(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, _event(0, {"trail": "meteor"}))
        await send(ws, _event(5, {"trail": "moving", "start_azimuth_deg": 0.0,
                                  "start_altitude_deg": 20.0, "end_azimuth_deg": 90.0,
                                  "end_altitude_deg": 40.0}))  # fmt: skip
        await send(ws, _event(9, {"trail": "dashed"}))
        await _requests(ws)
    page = await client.get(f"/cameras/{dev.device_id}/ueberfluege")
    assert page.status_code == 200
    assert "2 Überflüge: 2 unbekannt" in page.text
    assert "N 20° → O 40°" in page.text
    async with app.state.sessionmaker() as db:
        summary = await summarize(db, dev.device_id, NIGHT)
    assert summary.meteor_count == 1
    assert summary.overflights == "2 Überflüge: 2 unbekannt"
    assert "2 Überflüge" in summary.line()
    # On the map: the oriented one as a track, the other only in the list.
    assert page.text.count('class="ovf-track') == 1
    # The possible meteor is there too, as its own kind, for comparison.
    assert "1 Meteor zum Vergleich" in page.text
    assert 'data-ovf-group="meteor"' in page.text
    assert "2 ohne Himmelsrichtung" in page.text
    assert 'data-ovf-group="satellite"' in page.text
    # The event says what it is and leads back to the overflights.
    moving = event_id(EventKind.METEOR, START + dt.timedelta(minutes=5))
    link = re.search(rf'href="(/cameras/[^"]+/{moving}\?von=ueberfluege)"', page.text)
    assert link is not None
    event_page = await client.get(link.group(1).replace("&amp;", "&"))
    assert "<h1>Unbekanntes Objekt</h1>" in event_page.text
    assert f'href="/cameras/{dev.device_id}/ueberfluege?nacht={NIGHT}"' in event_page.text
    empty = await client.get(f"/cameras/{dev.device_id}/ueberfluege", params={"nacht": "20261001"})
    assert "Keine Überflüge" in empty.text


@pytest.mark.parametrize("variant", ["original"])
async def test_original_upload_needs_a_request(
    client: httpx.AsyncClient, live_app: str, variant: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    ev = _event(0, FRAME)
    async with device_ws(live_app, token):
        assert await _put(live_app, token, ev, EventVariant(variant), _jpeg(10, 10)) == 404


def test_tracks_follow_the_great_circle() -> None:
    from allskyhub_server.devices import overflight_map

    assert overflight_map.xy(90, 0) == (overflight_map.CENTRE, overflight_map.CENTRE)
    north = overflight_map.xy(0, 0)
    east = overflight_map.xy(0, 90)
    assert north[1] < overflight_map.CENTRE  # north at the top
    assert east[0] < overflight_map.CENTRE  # east on the left, as seen looking up
    # From low in the north to low in the south: over the zenith, not along the horizon.
    points = overflight_map.great_circle((10.0, 0.0), (10.0, 180.0), 4)
    assert points[2][0] == pytest.approx(90.0)
    assert points[0] == pytest.approx((10.0, 0.0), abs=1e-9)
    # Opposite points on the horizon have no single great circle: no division by zero.
    assert len(overflight_map.great_circle((0.0, 0.0), (0.0, 180.0), 4)) == 5
