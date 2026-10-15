"""Aircraft around the cameras (roadmap H3, SPEC §7.8)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import aircraft, public
from allskyhub_server.devices.aircraft import Fix, Tracks
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

LAT, LON = 48.31, 14.29
T0 = dt.datetime(2026, 10, 13, 20, 0, 0, tzinfo=dt.UTC)


def _fix(at: dt.datetime, lat: float, lon: float, alt_m: float = 10_000.0, **kw: Any) -> Fix:
    return Fix(at, lat, lon, alt_m, kw.get("track"), kw.get("speed"), kw.get("flight", "DLH4AB"))


def test_look_up_and_around() -> None:
    elev, az, km = aircraft.look(LAT, LON, LAT, LON, 10_000.0)
    assert elev > 89.9
    assert km < 0.01
    # 10 km up, 100 km north: about 5° up (the Earth's curve takes ~0.4°), due north.
    elev, az, km = aircraft.look(LAT, LON, LAT + 100 / 111.2, LON, 10_000.0)
    assert 4.9 < elev < 5.5
    assert az < 0.5 or az > 359.5
    assert 99 < km < 101
    elev, az, _ = aircraft.look(LAT, LON, LAT, LON + 0.5, 10_000.0)
    assert 85 < az < 95  # east


def test_interpolation_and_dead_reckoning() -> None:
    tracks = Tracks()
    tracks.add("adsb.lol", "3C6444", _fix(T0, 48.0, 14.0, track=90.0, speed=400.0))
    tracks.add("adsb.lol", "3c6444", _fix(T0 + dt.timedelta(seconds=10), 48.0, 14.1))
    mid = tracks.at(T0 + dt.timedelta(seconds=5))["3c6444"]
    assert abs(mid.lon - 14.05) < 1e-9
    assert tracks.at(T0 - dt.timedelta(seconds=1)) == {}
    # Past the newest: along the last track, at most 30 s.
    tracks.add("adsb.lol", "aaaaaa", _fix(T0, 48.0, 14.0, track=90.0, speed=400.0))
    ahead = tracks.at(T0 + dt.timedelta(seconds=20))["aaaaaa"]
    assert ahead.lon > 14.0  # flew east
    assert abs(ahead.lat - 48.0) < 0.01
    assert "aaaaaa" not in tracks.at(T0 + dt.timedelta(seconds=31))


def test_the_receiver_wins_while_fresh() -> None:
    tracks = Tracks()
    tracks.add("adsb.lol", "abc123", _fix(T0, 48.0, 14.0))
    tracks.add("adsb.lol", "abc123", _fix(T0 + dt.timedelta(seconds=60), 48.0, 14.6))
    tracks.add("receiver", "abc123", _fix(T0, 48.2, 14.0))
    tracks.add("receiver", "abc123", _fix(T0 + dt.timedelta(seconds=10), 48.2, 14.1))
    assert tracks.at(T0 + dt.timedelta(seconds=5))["abc123"].lat == 48.2
    later = tracks.at(T0 + dt.timedelta(seconds=50))["abc123"]
    assert later.lat == 48.0  # the receiver's newest is 40 s old: adsb.lol


def test_coverage_and_pruning() -> None:
    tracks = Tracks()
    for s in range(0, 121, 10):
        tracks.covered("cam", T0 + dt.timedelta(seconds=s))
    assert tracks.covers("cam", T0 + dt.timedelta(seconds=5), T0 + dt.timedelta(seconds=100))
    assert not tracks.covers("cam", T0 - dt.timedelta(seconds=5), T0 + dt.timedelta(seconds=50))
    assert not tracks.covers("cam", T0 + dt.timedelta(seconds=100), T0 + dt.timedelta(seconds=130))
    assert not tracks.covers("other", T0, T0 + dt.timedelta(seconds=10))
    tracks.covered("gap", T0)
    tracks.covered("gap", T0 + dt.timedelta(seconds=60))
    assert tracks.covers("gap", T0, T0 + dt.timedelta(seconds=60))  # a few failed fetches
    tracks.covered("gap", T0 + dt.timedelta(seconds=150))
    assert not tracks.covers("gap", T0, T0 + dt.timedelta(seconds=150))  # 90 s without data
    tracks.add("adsb.lol", "abc123", _fix(T0, LAT, LON))
    tracks.prune(T0 + aircraft.KEEP + dt.timedelta(seconds=1))
    assert tracks.at(T0) == {}
    assert not tracks.covers("cam", T0, T0)


def _flyby(tracks: Tracks) -> None:
    """An aircraft passing 40 km north of the camera from west to east, 10 km up."""
    for s in range(-60, 61, 10):
        lon = LON + s * 0.003  # about 13 km/min
        tracks.add("adsb.lol", "3c6444", _fix(T0 + dt.timedelta(seconds=s), LAT + 0.36, lon))
    tracks.add("adsb.lol", "ffffff", _fix(T0, LAT - 1.0, LON, flight=None))  # far south


def test_a_meteor_on_the_aircraft_path_is_named() -> None:
    tracks = Tracks()
    _flyby(tracks)
    a = tracks.planes(T0 - dt.timedelta(seconds=10), LAT, LON)
    b = tracks.planes(T0 + dt.timedelta(seconds=10), LAT, LON)
    plane_a = next(p for p in a if p.hex == "3c6444")
    plane_b = next(p for p in b if p.hex == "3c6444")
    ends = ((plane_a.elevation, plane_a.azimuth), (plane_b.elevation, plane_b.azimuth))
    found = aircraft.match(tracks, LAT, LON, T0, T0 + dt.timedelta(seconds=2), ends)
    assert found is not None
    assert found.detail == "DLH4AB 3c6444"
    # A track off to the side by 5°: no aircraft.
    off = ((plane_a.elevation + 5, plane_a.azimuth), (plane_b.elevation + 5, plane_b.azimuth))
    assert aircraft.match(tracks, LAT, LON, T0, T0 + dt.timedelta(seconds=2), off) is None
    # One end on the path, the other far away: not the aircraft.
    half = ((plane_a.elevation, plane_a.azimuth), (60.0, 200.0))
    assert aircraft.match(tracks, LAT, LON, T0, T0 + dt.timedelta(seconds=2), half) is None


def test_parse_adsb_lol() -> None:
    body = {
        "now": T0.timestamp() * 1000,
        "ac": [
            {"hex": "3C7424", "flight": "TUI1TX  ", "alt_baro": 8825, "gs": 255.4,
             "track": 323.76, "lat": 48.1, "lon": 12.4, "seen_pos": 0.5},
            {"hex": "~2a0001", "alt_baro": 30000, "lat": 48.2, "lon": 14.0},
            {"hex": "abcdef", "alt_baro": "ground", "lat": 48.0, "lon": 14.0},
            {"hex": "fedcba", "alt_baro": 1000, "lat": 48.0, "lon": 14.0, "seen_pos": 90},
            {"hex": "000001", "alt_baro": 1000},
        ],
    }  # fmt: skip
    fixes = dict(aircraft.parse(body, T0))
    assert set(fixes) == {"3c7424", "2a0001"}
    tui = fixes["3c7424"]
    assert tui.flight == "TUI1TX"
    assert tui.alt_m is not None
    assert abs(tui.alt_m - 8825 * 0.3048) < 1e-6
    assert tui.at == T0 - dt.timedelta(seconds=0.5)
    assert fixes["2a0001"].flight is None


def test_cameras_nearby_share_a_request() -> None:
    found = aircraft.areas([("a", 48.16, 14.03), ("b", 48.3, 14.3), ("c", 52.5, 13.4)])
    assert [a.devices for a in found] == [("a", "b"), ("c",)]
    assert found[0].nm > aircraft.RADIUS_KM / 1.852
    assert (found[0].lat, found[0].lon) == (48.2, 14.0)  # never the exact place
    assert found[1].nm <= round(aircraft.RADIUS_KM / 1.852) + 6


def test_who_needs_aircraft() -> None:
    status = {"settings": {"latitude": LAT, "longitude": LON, "detect_meteor": "normal"}}
    night = dt.datetime(2026, 10, 13, 21, 0, tzinfo=dt.UTC)
    noon = dt.datetime(2026, 10, 13, 11, 0, tzinfo=dt.UTC)
    assert aircraft.wants_aircraft(status, False, night) == (LAT, LON)
    assert aircraft.wants_aircraft(status, False, noon) is None
    assert aircraft.wants_aircraft(status, True, noon) == (LAT, LON)  # someone watches
    off = {"settings": {**status["settings"], "detect_meteor": "off"}}
    assert aircraft.wants_aircraft(off, False, night) is None
    assert aircraft.wants_aircraft({"settings": {}}, True, night) is None


def test_overlay_draws_aircraft_with_their_trail() -> None:
    from allskyhub_server.devices import sky_overlay
    from tests.test_sky_overlay import _status  # pyright: ignore[reportPrivateUsage]

    tracks = Tracks()
    _flyby(tracks)
    found = sky_overlay.build_aircraft(_status(), T0, tracks)
    assert found is not None
    # Nearest first; the one 111 km south is low (about 4°) but above the horizon.
    assert [a.text for a in found.items] == ["DLH4AB · 10,0 km", "ffffff · 10,0 km"]
    plane = found.items[0]
    assert 0 <= plane.x <= found.width
    assert 0 <= plane.y <= found.height
    assert plane.trail.startswith("M")
    assert plane.trail.count("L") == 6  # 60 s back in steps of 10 s
    assert found.credit == "Aircraft data: adsb.lol (ODbL)"
    assert sky_overlay.build_aircraft(_status(lens=None), T0, tracks) is None
    assert sky_overlay.build_aircraft(_status(), T0 + dt.timedelta(minutes=5), tracks) is not None


async def test_camera_page_has_the_switch(app: FastAPI, client: httpx.AsyncClient) -> None:
    from tests.test_sky_overlay import AT, _status  # pyright: ignore[reportPrivateUsage]

    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = _status()
        device.last_frame = {"captured_at": AT.isoformat()}
        device.latest_image_at = AT
        await db.commit()
    watch: aircraft.Watch = app.state.aircraft
    for s in range(-60, 61, 10):
        watch.tracks.add("adsb.lol", "3c6444", _fix(AT + dt.timedelta(seconds=s), LAT + 0.36, LON))
    page = (await client.get(f"/cameras/{dev.device_id}/live")).text
    assert 'data-overlay-toggle="air"' in page
    assert "DLH4AB · 10,0 km" in page
    assert "adsb.lol (ODbL)" in page

    # The public page: with the section aircraft (default on), and marks it as watched.
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        public.enable(device)
        slug = device.public_slug
        await db.commit()
    shown = (await client.get(f"/sky/{slug}/now")).text
    assert 'data-overlay-toggle="air"' in shown
    assert "DLH4AB · 10,0 km" in shown
    assert watch._is_watched(dev.device_id, dt.datetime.now(dt.UTC))  # pyright: ignore[reportPrivateUsage]
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        profile = public.profile(device)
        sections = profile.sections.model_copy(update={"aircraft": False})
        device.public_profile = profile.model_copy(update={"sections": sections}).model_dump()
        await db.commit()
    assert 'data-overlay-toggle="air"' not in (await client.get(f"/sky/{slug}/now")).text
