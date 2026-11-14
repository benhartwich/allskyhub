"""The camera page's sky tiles and orientation line (devices/sky_now.py, SPEC §6.3
status.sky and status.orientation)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update

from allskyhub_server.devices import sky_now
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

ORIENTATION = {
    "north_deg": 78.4,
    "mirrored": True,
    "solved_at": "2026-11-13T21:01:00+00:00",
    "stars": 412,
    "rms_deg": 0.08,
}
CLOUD_MAP = [0.9, 0.8, 0.2, 0.0, 0.0, None, 0.1, 0.6] + [0.0] * 16
SKY = {
    "at": "2026-11-13T22:07:00+00:00",
    "night_id": "20261113",
    "cloud_cover": 0.083,
    "sqm_mag": 20.63,
    "stars": 412,
    "cloud_map": CLOUD_MAP,
    "transparency": 0.12,
}


def test_orientation_levels() -> None:
    def level(rms: object) -> str | None:
        o = sky_now.orientation({"orientation": {**ORIENTATION, "rms_deg": rms}})
        return None if o is None else o.level

    assert [level(r) for r in (0.08, 0.2, 0.21, 0.4, 0.55, None)] == [
        "good", "good", "usable", "usable", "rough", "usable",
    ]  # fmt: skip
    o = sky_now.orientation({"orientation": ORIENTATION})
    assert o is not None
    assert (o.north_deg, o.stars, o.text) == (78, 412, "gut eingemessen")
    assert o.solved_at == dt.datetime(2026, 11, 13, 21, 1, tzinfo=dt.UTC)
    wrapped = sky_now.orientation({"orientation": {**ORIENTATION, "north_deg": 359.6}})
    assert wrapped is not None
    assert wrapped.north_deg == 0
    assert sky_now.orientation({"orientation": None}) is None
    assert sky_now.orientation({"orientation": {"north_deg": "x"}}) is None
    assert sky_now.orientation(None) is None


def test_sky_now_values_and_cloud_map() -> None:
    now = sky_now.sky_now({"sky": SKY})
    assert now is not None
    assert (now.stars, now.cloud_pct, now.sqm, now.transparency) == (412, 8, 20.63, 0.12)
    assert len(now.cells) == 24
    # Index 0: band 15–40°, sector north; index 5: south-west, not judgeable.
    assert now.cells[0].shade == 9
    assert now.cells[0].title == "N, 15–40°: 90 % bewölkt"
    assert now.cells[5].shade is None
    assert "nicht beurteilbar" in now.cells[5].title
    # East (sector 2) is drawn left, like the image seen from below.
    east = now.cells[2].path
    assert east.startswith("M-")
    # The top band's cells meet in the centre.
    assert now.cells[16].path.endswith("L0 0 Z")


def test_sky_now_partial_and_invalid() -> None:
    day = sky_now.sky_now({"sky": {"at": SKY["at"], "cloud_cover": 0.4, "stars": None}})
    assert day is not None
    assert (day.stars, day.cloud_pct, day.cells) == (None, 40, [])
    for bad in ([None] * 24, [0.1] * 23, "x"):
        now = sky_now.sky_now({"sky": {**SKY, "cloud_map": bad}})
        assert now is not None
        assert now.cells == []
    assert sky_now.sky_now({"sky": {"at": SKY["at"], "stars": True, "cloud_cover": None}}) is None
    assert sky_now.sky_now({"sky": None}) is None
    assert sky_now.sky_now(None) is None


async def test_live_shows_tiles_and_orientation(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    live = (await client.get(f"/cameras/{dev.device_id}/live")).text
    assert "Himmel jetzt" not in live
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(
                last_status={"sky": SKY, "orientation": ORIENTATION},
                latest_image_at=dt.datetime.now(dt.UTC),
            )
        )
        await db.commit()
    live = (await client.get(f"/cameras/{dev.device_id}/live")).text
    assert "Himmel jetzt" in live
    assert "Sterne gesehen" in live
    assert "8 %" in live
    assert "20,6" in live
    assert "0,12" in live
    assert live.count('class="cm-') == 24
    assert 'class="cm-na"' in live
    assert "gut eingemessen" in live
    assert "Abweichung 0,08°" in live
    assert "Norden bei 78°" in live
    assert "data-flat" not in live  # older agents send no flat


def test_flat_line() -> None:
    """SPEC §4.10, §6.3 status.flat (draft approved 2026-11-14)."""
    assert sky_now.flat({}) is None
    assert sky_now.flat({"flat": "x"}) is None
    measured = sky_now.flat({"flat": {"source": "twilight", "at": "2026-11-12T16:20:00Z",
                                      "max_gain": 1.42, "reason": None}})  # fmt: skip
    assert measured is not None
    assert (measured.level, measured.text, measured.detail) == (
        "good",
        "Flat gemessen",
        "Dämmerung",
    )
    assert (measured.max_gain, measured.at) == (
        1.42,
        dt.datetime(2026, 11, 12, 16, 20, tzinfo=dt.UTC),
    )
    night = sky_now.flat({"flat": {"source": "night", "max_gain": 1.31}})
    assert night is not None
    assert (night.level, night.text) == ("usable", "Flat geschätzt")
    none = sky_now.flat({"flat": {"source": None, "reason": "Objektiv korrigiert schon selbst"}})
    assert none is not None
    assert (none.level, none.text, none.detail, none.max_gain) == (
        "off", "kein Flat", "Objektiv korrigiert schon selbst", None)  # fmt: skip
    off = sky_now.flat({"settings": {"flat_correction": "off"}, "flat": {"source": "night"}})
    assert off is not None
    assert (off.level, off.text) == ("off", "Flat aus")


async def test_live_shows_the_flat(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    flat = {"source": "twilight", "at": dt.datetime.now(dt.UTC).isoformat(), "max_gain": 1.42}
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"orientation": ORIENTATION, "flat": flat},
                    latest_image_at=dt.datetime.now(dt.UTC))
        )  # fmt: skip
        await db.commit()
    live = (await client.get(f"/cameras/{dev.device_id}/live")).text
    assert "data-flat" in live
    assert "Flat gemessen" in live
    assert "Dämmerung" in live
    assert "Rand bis \u00d71,42" in live


def test_cloud_map_turns_like_the_image() -> None:
    """SPEC §6.3: image direction of azimuth A is north - A when mirrored, else north + A."""
    usual = sky_now.sky_now({"sky": SKY, "orientation": {**ORIENTATION, "north_deg": 0.0}})
    assert usual is not None
    assert {c[0]: c[1:] for c in usual.compass} == {
        "N": (0.0, -91.0), "O": (-91.0, 0.0), "S": (0.0, 91.0), "W": (91.0, 0.0),
    }  # fmt: skip
    turned = sky_now.sky_now(
        {"sky": SKY, "orientation": {**ORIENTATION, "north_deg": 90.0, "mirrored": False}}
    )
    assert turned is not None
    assert {c[0]: c[1:] for c in turned.compass} == {
        "N": (91.0, 0.0), "O": (0.0, 91.0), "S": (-91.0, 0.0), "W": (0.0, -91.0),
    }  # fmt: skip
    # Not mirrored: the arcs run clockwise (sweep 1) outside, back counter-clockwise inside.
    assert " 0 0 1 " in turned.cells[0].path
    assert " 0 0 0 " in turned.cells[0].path
    # The north cell of the lowest band starts right of the centre (north at 90° in the image).
    x, _ = turned.cells[0].path[1:].split(" ")[:2]
    assert float(x) > 70
